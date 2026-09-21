#!/usr/bin/env bash
# Live demo runner for CareScaffold v1.0
# Starts uvicorn, runs all demo curl commands, captures output, keeps server running
set +e

PROJECT_DIR="/home/z/my-project/carescaffold"
LOG_FILE="/tmp/carescaffold_uvicorn.log"
OUTPUT_FILE="/tmp/carescaffold_demo_output.txt"
PID_FILE="/tmp/carescaffold_uvicorn.pid"

# Kill any stale uvicorn
pkill -f "uvicorn app:app" 2>/dev/null || true
sleep 1

# Clear logs
rm -f "$LOG_FILE" "$OUTPUT_FILE" "$PID_FILE"

# Start uvicorn in a fully detached process
cd "$PROJECT_DIR"
unset DATABASE_URL
setsid uvicorn app:app --host 127.0.0.1 --port 8000 --log-level warning > "$LOG_FILE" 2>&1 < /dev/null &
UVICORN_PID=$!
disown
echo $UVICORN_PID > "$PID_FILE"

# Wait for server to come up (poll /health)
echo "=== starting uvicorn (PID $UVICORN_PID) ===" | tee -a "$OUTPUT_FILE"
for i in {1..30}; do
    if curl -s -o /dev/null --max-time 1 http://127.0.0.1:8000/health 2>/dev/null; then
        echo "  server up after ${i}s" | tee -a "$OUTPUT_FILE"
        break
    fi
    sleep 1
done

# Verify uvicorn still alive
if ! ps -p $UVICORN_PID > /dev/null 2>&1; then
    echo "ERROR: uvicorn died during startup" | tee -a "$OUTPUT_FILE"
    cat "$LOG_FILE" | tee -a "$OUTPUT_FILE"
    exit 1
fi

echo "" | tee -a "$OUTPUT_FILE"
echo "==========================================" | tee -a "$OUTPUT_FILE"
echo "  CareScaffold v1.0 — Live Demo" | tee -a "$OUTPUT_FILE"
echo "  Server: http://127.0.0.1:8000" | tee -a "$OUTPUT_FILE"
echo "==========================================" | tee -a "$OUTPUT_FILE"

run_demo() {
    local title="$1"
    local cmd="$2"
    echo "" | tee -a "$OUTPUT_FILE"
    echo "=== $title ===" | tee -a "$OUTPUT_FILE"
    echo "$ $cmd" | tee -a "$OUTPUT_FILE"
    eval "$cmd" 2>&1 | tee -a "$OUTPUT_FILE"
}

# 1. Health
run_demo "1. GET /health" \
    "curl -s http://127.0.0.1:8000/health | python3 -m json.tool"

# 2. CapabilityStatement
run_demo "2. GET /fhir/metadata (FHIR R4 CapabilityStatement)" \
    "curl -s http://127.0.0.1:8000/fhir/metadata | python3 -c 'import sys, json; cs = json.load(sys.stdin); print(f\"  resourceType: {cs[\\\"resourceType\\\"]}\"); print(f\"  fhirVersion: {cs[\\\"fhirVersion\\\"]}\"); print(f\"  format: {cs[\\\"format\\\"]}\"); print(\"  resources exposed:\"); [print(f\"    - {r[\\\"type\\\"]}: {[i[\\\"code\\\"] for i in r[\\\"interaction\\\"]]} \") for r in cs[\\\"rest\\\"][0][\\\"resource\\\"]]'\"

# 3. Patient list
run_demo "3. GET /fhir/Patient (all 20 T2D patients)" \
    "curl -s http://127.0.0.1:8000/fhir/Patient | python3 -c 'import sys, json; b = json.load(sys.stdin); print(f\"  resourceType: {b[\\\"resourceType\\\"]}\"); print(f\"  type: {b[\\\"type\\\"]}\"); print(f\"  total: {b[\\\"total\\\"]}\"); print(f\"  first patient id: {b[\\\"entry\\\"][0][\\\"resource\\\"][\\\"id\\\"]}\")'"

# Get first patient ID for subsequent calls
FIRST_PID=$(curl -s http://127.0.0.1:8000/fhir/Patient | python3 -c "import sys, json; print(json.load(sys.stdin)['entry'][0]['resource']['id'])")
echo "  (using FIRST_PID=$FIRST_PID for next demos)" | tee -a "$OUTPUT_FILE"

# 4. Single patient
run_demo "4. GET /fhir/Patient/{id}" \
    "curl -s http://127.0.0.1:8000/fhir/Patient/$FIRST_PID | python3 -c 'import sys, json; p = json.load(sys.stdin); print(f\"  id: {p[\\\"id\\\"]}\"); print(f\"  name: {p[\\\"name\\\"][0].get(\\\"given\\\", [])} {p[\\\"name\\\"][0][\\\"family\\\"]}\"); print(f\"  gender: {p.get(\\\"gender\\\")}\"); print(f\"  birthDate: {p.get(\\\"birthDate\\\")}\")'"

# 5. Condition search
run_demo "5. GET /fhir/Condition?patient=X" \
    "curl -s 'http://127.0.0.1:8000/fhir/Condition?patient=$FIRST_PID' | python3 -c 'import sys, json; b = json.load(sys.stdin); print(f\"  total conditions: {b[\\\"total\\\"]}\"); print(f\"  first condition: {b[\\\"entry\\\"][0][\\\"resource\\\"][\\\"code\\\"][\\\"coding\\\"][0][\\\"display\\\"][:60]}\")'"

# 6. A1C observations
run_demo "6. GET /fhir/Observation?patient=X (A1C observations)" \
    "curl -s 'http://127.0.0.1:8000/fhir/Observation?patient=$FIRST_PID' | python3 -c 'import sys, json; b = json.load(sys.stdin); print(f\"  total A1C observations: {b[\\\"total\\\"]}\"); o = b[\\\"entry\\\"][0][\\\"resource\\\"]; vq = o.get(\\\"valueQuantity\\\", {}); print(f\"  first A1C: code={o[\\\"code\\\"][\\\"coding\\\"][0][\\\"code\\\"]} value={vq.get(\\\"value\\\")} {vq.get(\\\"unit\\\")}\")'"

# 7. 404 handling
run_demo "7. GET /fhir/Patient/nonexistent-id → 404" \
    "curl -s -o /dev/null -w '  HTTP %{http_code}\n' http://127.0.0.1:8000/fhir/Patient/nonexistent-id"

# 8. Inbound safety - emergency
run_demo "8. POST /scaffold — chest pain emergency (inbound safety bypasses LLM)" \
    "curl -s -X POST http://127.0.0.1:8000/scaffold -H 'Content-Type: application/json' -d '{\"question\": \"I am having chest pain and it is spreading to my left arm.\", \"persona\": \"foundational\"}' | python3 -c 'import sys, json; r = json.load(sys.stdin); print(f\"  escalated: {r[\\\"escalated\\\"]}\"); print(f\"  safety_layer: {r[\\\"safety_layer\\\"]}\"); print(f\"  response: {r[\\\"response\\\"]}\")'"

# 9. Inbound safety - self-harm
run_demo "9. POST /scaffold — self-harm indicator (inbound safety bypasses LLM)" \
    "curl -s -X POST http://127.0.0.1:8000/scaffold -H 'Content-Type: application/json' -d '{\"question\": \"I want to hurt myself. I have a plan and I am scared.\", \"persona\": \"foundational\"}' | python3 -c 'import sys, json; r = json.load(sys.stdin); print(f\"  escalated: {r[\\\"escalated\\\"]}\"); print(f\"  safety_layer: {r[\\\"safety_layer\\\"]}\"); print(f\"  response: {r[\\\"response\\\"]}\")'"

# 10. Severe hypoglycemia
run_demo "10. POST /scaffold — severe hypoglycemia (inbound safety)" \
    "curl -s -X POST http://127.0.0.1:8000/scaffold -H 'Content-Type: application/json' -d '{\"question\": \"My sugar is 38 and I cannot think straight or remember my own name.\", \"persona\": \"foundational\"}' | python3 -c 'import sys, json; r = json.load(sys.stdin); print(f\"  escalated: {r[\\\"escalated\\\"]}\"); print(f\"  safety_layer: {r[\\\"safety_layer\\\"]}\"); print(f\"  response: {r[\\\"response\\\"]}\")'"

echo "" | tee -a "$OUTPUT_FILE"
echo "=== DONE — server still running on http://127.0.0.1:8000 ===" | tee -a "$OUTPUT_FILE"
echo "=== uvicorn PID: $UVICORN_PID ===" | tee -a "$OUTPUT_FILE"
echo "" | tee -a "$OUTPUT_FILE"
echo "To stop: pkill -f 'uvicorn app:app'" | tee -a "$OUTPUT_FILE"

# Show the full output
echo ""
echo "===== FULL OUTPUT ====="
cat "$OUTPUT_FILE"
