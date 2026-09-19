"""LLM Router — single point of model identifier access (spec §4.3).

Spec §1.2.5: NEVER hardcode model strings in business logic. The router
reads from /config/model_registry.yaml via /core/config.py. Adding or
changing a model is a one-line YAML edit; the next router call picks
it up via the registry's mtime-based hot-reload.

v2.5 DEVIATION (2026-09-19): Operator directed a swap from Anthropic
Claude to GLM (z-ai-web-dev-sdk). The z-ai SDK is Node.js, so the
router calls it via subprocess (`z-ai chat` CLI). This is a portfolio-
demo simplification; production would use a long-running Node sidecar
or HTTP service. Documented in /docs/phase2_metrics.md.

Trade-off (spec §4.4.2 already acknowledged): GLM lacks reliable
structured output via tool-use. The safety judge uses prompt
engineering + first-label parsing instead of Anthropic's enum enforcement.
Fail-safe default: any judge error or unparseable response → BLOCK_DIRECTIVE.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass
from typing import Any

from core.config import embeddings_config, get_settings, models_for_role, retry_config, timeouts

log = logging.getLogger(__name__)


# ── z-ai CLI bridge ──────────────────────────────────────────────
_ZAI_BIN = shutil.which("z-ai") or "z-ai"


async def _zai_chat(prompt: str, system: str | None = None, retries: int = 2) -> str:
    """Call `z-ai chat` via subprocess and return the assistant content.

    Uses the z-ai-web-dev-sdk's CLI (Node.js) from Python. This is the
    simplest interop for a portfolio demo; production would use a
    persistent Node sidecar or HTTP service for lower latency.

    Retries up to `retries` times on transient CLI failures (non-zero
    exit codes) with a 1s backoff between attempts. This handles
    occasional API timeouts from the upstream provider.

    Returns the assistant message content as a string.
    Raises RuntimeError on persistent failure.
    """
    import asyncio as _asyncio
    import tempfile

    last_err: str | None = None
    for attempt in range(retries + 1):
        cmd = [_ZAI_BIN, "chat", "-p", prompt]
        if system:
            cmd.extend(["-s", system])
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".json", delete=False) as f:
            out_path = f.name
        cmd.extend(["-o", out_path])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                err = stderr.decode("utf-8", errors="replace") if stderr else "(no stderr)"
                last_err = f"z-ai chat exited {proc.returncode}: {err[:300]}"
                if attempt < retries:
                    await _asyncio.sleep(1.0 * (attempt + 1))
                    continue
                raise RuntimeError(last_err)

            try:
                payload = json.loads(open(out_path).read())
            except (json.JSONDecodeError, FileNotFoundError) as e:
                last_err = f"Could not parse z-ai output: {e}"
                if attempt < retries:
                    await _asyncio.sleep(1.0 * (attempt + 1))
                    continue
                raise RuntimeError(last_err)

            choices = payload.get("choices") or []
            if not choices:
                last_err = f"z-ai response had no choices: {payload}"
                if attempt < retries:
                    await _asyncio.sleep(1.0 * (attempt + 1))
                    continue
                raise RuntimeError(last_err)
            content = choices[0].get("message", {}).get("content", "")
            if not content:
                last_err = f"z-ai response had empty content: {payload}"
                if attempt < retries:
                    await _asyncio.sleep(1.0 * (attempt + 1))
                    continue
                raise RuntimeError(last_err)
            return content.strip()
        finally:
            try:
                import os
                os.unlink(out_path)
            except OSError:
                pass
    raise RuntimeError(f"_zai_chat failed after {retries + 1} attempts: {last_err}")


@dataclass
class FallbackEvent:
    """Structured record of a fallback trigger for observability."""

    role: str
    primary: str
    fallback: str
    reason: str
    latency_ms: int


# Allowed verdicts for the outbound safety judge (spec §4.4.2)
JUDGE_VERDICTS = ("SAFE", "BLOCK_DIAGNOSIS", "BLOCK_DOSAGE", "BLOCK_DIRECTIVE")


# ── Safety judge system prompt ──────────────────────────────────
# Constrain output: model must START its response with exactly one of
# the four labels. We parse the first whitespace-separated token.
# Spec §4.4.2: GLM lacks reliable tool-use structured output, so we
# use prompt engineering + first-label parsing instead.
_JUDGE_SYSTEM = (
    "You are a strict safety classifier for a patient-education chatbot. "
    "Reply with exactly one of these four labels, and nothing else: "
    "SAFE, BLOCK_DIAGNOSIS, BLOCK_DOSAGE, BLOCK_DIRECTIVE. "
    "Do not include any other text, punctuation, or explanation."
)


class LLMRouter:
    """Routes model calls through config-driven role lookup.

    v2.5: Implementation uses GLM via the z-ai-web-dev-sdk CLI.
    """

    async def call_generation(self, prompt: str, **kwargs: Any) -> str:
        """Phase 3 generation call. TODO: wire in Phase 3."""
        system = kwargs.get("system", "You are a helpful patient-education assistant.")
        return await _zai_chat(prompt, system=system)

    async def call_safety_judge(self, response_text: str) -> str:
        """Phase 2 Layer 2 — GLM-4-Plus judge call with 3-strike retry.

        Spec §4.4.2 originally called for Claude Haiku 4.5 with tool-use
        structured output. v2.5 swap to GLM means we use prompt
        engineering + first-label parsing instead.

        v2.5.1 (post held-out failure): GLM has occasional nondeterminism
        on borderline-unsafe responses. To prevent a single bad call from
        leaking an unsafe response, we retry up to 3 times. If any retry
        returns a BLOCK_* verdict, we treat the response as blocked
        (fail-safe: prefer false-positive blocks over false-negative
        releases). Only if ALL 3 retries return SAFE do we release.

        v2.5.2 (rate-limit handling): On 429 (rate limit) from the API,
        retry with exponential backoff up to 30s. Don't fail-safe on
        rate-limit (it's transient, not a real safety verdict); only
        fail-safe to BLOCK_DIRECTIVE if all retries genuinely fail to
        return a parseable verdict.

        Returns one of: SAFE, BLOCK_DIAGNOSIS, BLOCK_DOSAGE, BLOCK_DIRECTIVE.
        """
        from llm.prompts import OUTBOUND_JUDGE_V1

        prompt = OUTBOUND_JUDGE_V1.replace("{response_text}", response_text)
        prompt += (
            "\n\nReply with exactly one of: SAFE, BLOCK_DIAGNOSIS, "
            "BLOCK_DOSAGE, BLOCK_DIRECTIVE. Nothing else."
        )

        # 3 verdict attempts + up to 30s backoff for 429s.
        verdicts: list[str] = []
        max_judge_attempts = 3
        for attempt in range(max_judge_attempts):
            raw = await self._zai_chat_with_rate_limit(prompt, system=_JUDGE_SYSTEM)
            if raw is None:
                # Persistent rate limit after exponential backoff — give up
                # and fail-safe. This branch is rare and indicates a
                # sustained outage, not a verdict.
                log.error("Judge unavailable after rate-limit backoff; failing safe.")
                verdicts.append("BLOCK_DIRECTIVE")
                continue

            first_token = raw.strip().split()[0] if raw.strip() else ""
            first_token = first_token.rstrip(".,;:!?").upper()

            if first_token in JUDGE_VERDICTS:
                verdicts.append(first_token)
                continue

            # Try to find any of the 4 labels anywhere in the response
            found = None
            for label in JUDGE_VERDICTS:
                if label in raw.upper():
                    found = label
                    break
            if found:
                log.warning(
                    "Judge did not start with a label; found '%s' in response. "
                    "Raw: %s", found, raw[:200]
                )
                verdicts.append(found)
            else:
                log.warning("Judge response unparseable. Raw: %s", raw[:200])
                verdicts.append("BLOCK_DIRECTIVE")

        # Fail-safe aggregation: if ANY of the attempts returned a BLOCK_*,
        # return the most-seen BLOCK_* verdict.
        block_verdicts = [v for v in verdicts if v != "SAFE"]
        if not block_verdicts:
            return "SAFE"

        from collections import Counter
        most_common = Counter(block_verdicts).most_common(1)[0][0]
        if len(set(verdicts)) > 1:
            log.warning(
                "Judge nondeterminism on response. Verdicts: %s. Fail-safe: %s",
                verdicts, most_common,
            )
        return most_common

    async def _zai_chat_with_rate_limit(self, prompt: str, system: str | None = None) -> str | None:
        """Call _zai_chat with exponential backoff on 429 rate limits.

        Returns the assistant content on success, or None if the API
        remains rate-limited after 4 attempts (1s, 2s, 4s, 8s).
        Distinguishes 429 from real API errors so the caller can decide
        how to handle a sustained outage.
        """
        import asyncio as _asyncio
        backoff = 1.0
        for attempt in range(4):
            try:
                return await _zai_chat(prompt, system=system, retries=0)
            except RuntimeError as e:
                msg = str(e)
                if "429" in msg or "Too many requests" in msg:
                    log.info("z-ai 429 rate limit; backing off %.1fs", backoff)
                    await _asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                # Non-429 error — re-raise (caller will fail-safe)
                raise
        return None

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError(
            "Document embedding wired in Phase 3 (spec §4.2 ingest)."
        )

    async def embed_query(self, text: str) -> list[float]:
        raise NotImplementedError(
            "Query embedding wired in Phase 3 (spec §4.2 retrieve)."
        )

    # ── Config accessors (used by tests + Phase 2/3 implementations) ──
    def generation_models(self) -> tuple[str, str | None]:
        return models_for_role("generation")

    def judge_model(self) -> str:
        primary, _ = models_for_role("safety_judge")
        return primary

    def embedding_model(self) -> tuple[str, int]:
        cfg = embeddings_config()
        return cfg.primary, cfg.output_dimension

    def latency_failover_ms(self) -> int:
        return int(timeouts().get("latency_failover_ms", 2500))

    def retry_max_attempts(self) -> int:
        return int(retry_config().get("max_attempts", 2))


# Module-level singleton for service code
router = LLMRouter()
