#!/usr/bin/env python3
"""Generate the 150+ case adversarial PHI corpus per spec §4.1.

Per spec §11.1 ("Build the test corpus for Phase 1 before the redaction
logic it tests. Building both simultaneously invites unconsciously
tuning the corpus to the implementation."), this script generates the
corpus INDEPENDENTLY of the redactor implementation.

Output: tests/adversarial/phi_corpus.jsonl

Each line is a JSON object:
  {
    "id": "phi-001",
    "input": "<text containing PHI>",
    "expected_spans": [
      {"span": "John Smith", "category": "names", "start": 0, "end": 10},
      ...
    ],
    "expected_redactions": ["names"],
    "category": "names",  # primary category for this case
    "edge_case": false,   # true if this is an edge case per spec §4.1
    "notes": "..."
  }

Covers all 18 HIPAA Safe Harbor categories (45 CFR 164.514(b)(2)).
~8-9 cases per category = 150+ total, including edge cases the spec
calls out explicitly:
  - MRN embedded mid-sentence (category 8)
  - Device identifier referenced casually ("my Dexcom G7") (category 13)
  - Relative dates ("three days after my mom's surgery") (category 3)
  - Nicknames, misspelled names, addresses mid-sentence (category 1, 2)
  - Non-US phone formats (category 4)
  - Identifiers patients use for each other, not just themselves (all)
"""
from __future__ import annotations

import json
from pathlib import Path

OUTPUT = Path(__file__).resolve().parent.parent / "tests" / "adversarial" / "phi_corpus.jsonl"


# Helpers
def case(cid, text, spans, categories, primary, notes="", edge=False):
    """Build one corpus case.

    spans: list of (span_text, category) — what should be redacted
    categories: list of all categories present (may include overlaps)
    primary: the main category this case is testing
    """
    expected_spans = []
    cursor = 0
    for span_text, cat in spans:
        idx = text.find(span_text, cursor)
        if idx == -1:
            # span not found — raise so we catch corpus bugs immediately
            raise ValueError(f"Span {span_text!r} not found in text: {text!r}")
        expected_spans.append({
            "span": span_text,
            "category": cat,
            "start": idx,
            "end": idx + len(span_text),
        })
        cursor = idx + len(span_text)
    return {
        "id": cid,
        "input": text,
        "expected_spans": expected_spans,
        "expected_redactions": categories,
        "category": primary,
        "edge_case": edge,
        "notes": notes,
    }


# Build the corpus — 150+ cases across 18 categories
cases = []

# ── Category 1: Names (patient, doctor, hospital) ─────────────────
cases += [
    case("phi-001", "My doctor Dr. Sarah Chen said my A1C is too high.",
         [("Dr. Sarah Chen", "names")], ["names"], "names",
         "Doctor name with title"),
    case("phi-002", "I saw John Smith at the diabetes clinic yesterday.",
         [("John Smith", "names")], ["names"], "names",
         "Patient name mid-sentence"),
    case("phi-003", "Hi, I'm Maria Rodriguez and I have a question about my meds.",
         [("Maria Rodriguez", "names")], ["names"], "names",
         "Self-introduction at start of message"),
    case("phi-004", "My wife Emily takes care of my injections.",
         [("Emily", "names")], ["names"], "names",
         "Family member name"),
    case("phi-005", "I go to Mercy Hospital for my checkups.",
         [("Mercy Hospital", "names")], ["names"], "names",
         "Hospital name"),
    case("phi-006", "My buddy Big Mike also has diabetes and he said metformin helped him.",
         [("Big Mike", "names")], ["names"], "names",
         "Nickname as identifier (edge case per spec §4.1)", edge=True),
    case("phi-007", "Dr. O'Brien told me to check my sugar more often.",
         [("Dr. O'Brien", "names")], ["names"], "names",
         "Apostrophe in name"),
    case("phi-008", "I talked to my friend Jon (he's T2D too) about side effects.",
         [("Jon", "names")], ["names"], "names",
         "Casual peer identifier — first name only, parenthetical context",
         edge=True),
]

# ── Category 2: Geographic subdivisions smaller than a state ─────
cases += [
    case("phi-009", "I live at 1234 Oak Street, Springfield, IL 62704.",
         [("1234 Oak Street", "geographic"), ("Springfield", "geographic"), ("62704", "geographic")],
         ["geographic"], "geographic",
         "Full street address + city + ZIP"),
    case("phi-010", "The patient is from Brooklyn, NY.",
         [("Brooklyn", "geographic")], ["geographic"], "geographic",
         "Borough — smaller than a state"),
    case("phi-011", "My ZIP code is 90210.",
         [("90210", "geographic")], ["geographic"], "geographic",
         "ZIP only — first 3 digits identify region"),
    case("phi-012", "I moved from Cambridge, MA to Somerville last year.",
         [("Cambridge", "geographic"), ("Somerville", "geographic")],
         ["geographic"], "geographic",
         "Two cities in one message — both must be caught"),
    case("phi-013", "I work at the clinic on 5th Avenue in Manhattan.",
         [("5th Avenue", "geographic"), ("Manhattan", "geographic")],
         ["geographic"], "geographic",
         "Street + borough"),
    case("phi-014", "We live in a small town called Cedar Falls (pop. 40000).",
         [("Cedar Falls", "geographic")], ["geographic"], "geographic",
         "Small town named mid-sentence (edge case per spec §4.1)", edge=True),
    case("phi-015", "My address is 789 Elm Drive, Apt 4B, Madison Wisconsin 53703.",
         [("789 Elm Drive", "geographic"), ("Madison", "geographic"), ("53703", "geographic")],
         ["geographic"], "geographic",
         "Address with apartment number"),
    case("phi-016", "I'm originally from a village in Weld County, Colorado.",
         [("Weld County", "geographic")], ["geographic"], "geographic",
         "County name — subdivision smaller than state"),
]

# ── Category 3: Dates (except year) and ages > 89 ─────────────────
cases += [
    case("phi-017", "I was born on March 15, 1962.",
         [("March 15, 1962", "dates")], ["dates"], "dates",
         "Full birth date — month+day+year"),
    case("phi-018", "My A1C test was on 09/12/2024.",
         [("09/12/2024", "dates")], ["dates"], "dates",
         "Numeric date format MM/DD/YYYY"),
    case("phi-019", "I was admitted to the hospital on July 4th.",
         [("July 4th", "dates")], ["dates"], "dates",
         "Date with ordinal suffix"),
    case("phi-020", "I turned 92 last month.",
         [("92", "dates")], ["dates"], "dates",
         "Age over 89 — explicitly called out in spec §4.1"),
    case("phi-021", "My grandmother is 95 and also has diabetes.",
         [("95", "dates")], ["dates"], "dates",
         "Age over 89 for a relative (not the patient themselves)"),
    case("phi-022", "Three days after my mom's surgery I felt dizzy.",
         [("Three days after my mom's surgery", "dates")], ["dates"], "dates",
         "Relative date — spec §4.1 edge case: requires inferring a date is present at all",
         edge=True),
    case("phi-023", "I started metformin last Tuesday.",
         [("last Tuesday", "dates")], ["dates"], "dates",
         "Relative day of week — needs temporal reasoning", edge=True),
    case("phi-024", "I've been taking this medicine since January 8th.",
         [("January 8th", "dates")], ["dates"], "dates",
         "Date without year — month+day only"),
]

# ── Category 4: Telephone numbers ─────────────────────────────────
cases += [
    case("phi-025", "Call me at (555) 123-4567 if you need anything.",
         [("(555) 123-4567", "phone")], ["phone"], "phone",
         "US format with parens"),
    case("phi-026", "My phone is 555-987-6543.",
         [("555-987-6543", "phone")], ["phone"], "phone",
         "Standard US format"),
    case("phi-027", "Reach me at +1-555-123-4567.",
         [("+1-555-123-4567", "phone")], ["phone"], "phone",
         "Country code prefix"),
    case("phi-028", "My mobile is 5551234567.",
         [("5551234567", "phone")], ["phone"], "phone",
         "10 digits no separators"),
    case("phi-029", "You can call me at +44 20 7946 0958 (I'm visiting from London).",
         [("+44 20 7946 0958", "phone")], ["phone"], "phone",
         "UK format — spec §4.1 edge case: non-US phone formats", edge=True),
    case("phi-030", "My home number is 555.123.4567.",
         [("555.123.4567", "phone")], ["phone"], "phone",
         "Dots as separators"),
    case("phi-031", "I can be reached at 555 867 5309 after 6pm.",
         [("555 867 5309", "phone")], ["phone"], "phone",
         "Spaces as separators"),
    case("phi-032", "Doctor's office: (555) 246-8135, fax: (555) 246-8136.",
         [("(555) 246-8135", "phone"), ("(555) 246-8136", "fax")],
         ["phone", "fax"], "phone",
         "Phone + fax in same message"),
]

# ── Category 5: Fax numbers ───────────────────────────────────────
cases += [
    case("phi-033", "Fax my records to 555-123-4567.",
         [("555-123-4567", "fax")], ["fax"], "fax",
         "Fax number in fax context"),
    case("phi-034", "My fax number is (555) 222-3333.",
         [("(555) 222-3333", "fax")], ["fax"], "fax",
         "Fax with parens"),
    case("phi-035", "Send the prescription to fax 5551234567.",
         [("5551234567", "fax")], ["fax"], "fax",
         "Fax without separators"),
    case("phi-036", "Fax: 1-800-555-0199",
         [("1-800-555-0199", "fax")], ["fax"], "fax",
         "Toll-free fax"),
    case("phi-037", "Records can be faxed to my office at +1-555-246-8100.",
         [("+1-555-246-8100", "fax")], ["fax"], "fax",
         "Fax with country code"),
    case("phi-038", "My clinic's fax machine broke so use 555.987.6543 instead.",
         [("555.987.6543", "fax")], ["fax"], "fax",
         "Fax with dot separators mid-sentence"),
]

# ── Category 6: Email addresses ───────────────────────────────────
cases += [
    case("phi-039", "Email me at john.smith@example.com for the records.",
         [("john.smith@example.com", "email")], ["email"], "email",
         "Standard email"),
    case("phi-040", "My email is mary.jones+diabetes@gmail.com.",
         [("mary.jones+diabetes@gmail.com", "email")], ["email"], "email",
         "Email with plus-addressing"),
    case("phi-041", "Send the report to dr.chen@hospital.org.",
         [("dr.chen@hospital.org", "email")], ["email"], "email",
         "Doctor email"),
    case("phi-042", "Reach me at patient123@mail.example",
         [("patient123@mail.example", "email")], ["email"], "email",
         "Email with new TLD"),
    case("phi-043", "My contact is first.last@sub.domain.example.",
         [("first.last@sub.domain.example", "email")], ["email"], "email",
         "Email with subdomain"),
    case("phi-044", "Update my address: new.email@example.gov",
         [("new.email@example.gov", "email")], ["email"], "email",
         "Email with .gov TLD"),
]

# ── Category 7: Social Security numbers ──────────────────────────
cases += [
    case("phi-045", "My SSN is 123-45-6789.",
         [("123-45-6789", "ssn")], ["ssn"], "ssn",
         "Standard SSN format"),
    case("phi-046", "I wrote it as 123456789 on the form.",
         [("123456789", "ssn")], ["ssn"], "ssn",
         "9 digits no dashes"),
    case("phi-047", "SSN: 123 45 6789",
         [("123 45 6789", "ssn")], ["ssn"], "ssn",
         "SSN with spaces"),
    case("phi-048", "Last four of my SSN are 6789 if that helps.",
         [("6789", "ssn")], ["ssn"], "ssn",
         "Last 4 only — partial SSN, may or may not be PHI depending on context; include as edge case",
         edge=True),
    case("phi-049", "My insurance asked for my social security number: 987-65-4321.",
         [("987-65-4321", "ssn")], ["ssn"], "ssn",
         "SSN with context label"),
    case("phi-050", "I think I typed 111-22-3333 into the wrong field.",
         [("111-22-3333", "ssn")], ["ssn"], "ssn",
         "SSN mid-sentence with surrounding text"),
]

# ── Category 8: Medical record numbers ────────────────────────────
cases += [
    case("phi-051", "My MRN is MRN-123456.",
         [("MRN-123456", "mrn")], ["mrn"], "mrn",
         "MRN with prefix"),
    case("phi-052", "My medical record number is 12345678.",
         [("12345678", "mrn")], ["mrn"], "mrn",
         "8-digit number with context"),
    case("phi-053", "The chart number for my visit was MRN: 99887766.",
         [("MRN: 99887766", "mrn")], ["mrn"], "mrn",
         "MRN with colon"),
    case("phi-054", "I overheard them say my record number is 112233 but I'm not sure what that means.",
         [("112233", "mrn")], ["mrn"], "mrn",
         "Record number referenced mid-sentence — spec §4.1 edge case: MRN embedded mid-sentence",
         edge=True),
    case("phi-055", "Hospital ID 555-1234 was on my wristband.",
         [("555-1234", "mrn")], ["mrn"], "mrn",
         "Hospital ID format"),
    case("phi-056", "Patient ID ABC-12345 was on every form.",
         [("ABC-12345", "mrn")], ["mrn"], "mrn",
         "Alphanumeric MRN"),
]

# ── Category 9: Health plan beneficiary numbers ───────────────────
cases += [
    case("phi-057", "My insurance ID is BCBS-123456789.",
         [("BCBS-123456789", "beneficiary")], ["beneficiary"], "beneficiary",
         "Insurance ID with carrier prefix"),
    case("phi-058", "Medicare number: 1A2B3C4D5E",
         [("1A2B3C4D5E", "beneficiary")], ["beneficiary"], "beneficiary",
         "Medicare number format"),
    case("phi-059", "My member ID is XY123456789.",
         [("XY123456789", "beneficiary")], ["beneficiary"], "beneficiary",
         "Member ID"),
    case("phi-060", "Health plan ID: HMO-9876543.",
         [("HMO-9876543", "beneficiary")], ["beneficiary"], "beneficiary",
         "HMO plan ID"),
    case("phi-061", "I have a Medicaid ID M1234567.",
         [("M1234567", "beneficiary")], ["beneficiary"], "beneficiary",
         "Medicaid ID with letter prefix"),
    case("phi-062", "Policy number POL-2024-555123.",
         [("POL-2024-555123", "beneficiary")], ["beneficiary"], "beneficiary",
         "Policy number format"),
]

# ── Category 10: Account numbers ──────────────────────────────────
cases += [
    case("phi-063", "My bank account is 1234567890.",
         [("1234567890", "account")], ["account"], "account",
         "Bank account number"),
    case("phi-064", "Routing 123456789, account 9876543210.",
         [("123456789", "account"), ("9876543210", "account")],
         ["account"], "account",
         "Routing + account"),
    case("phi-065", "My payment account is AC-5551234.",
         [("AC-5551234", "account")], ["account"], "account",
         "Account with prefix"),
    case("phi-066", "Account number 0000111122223333.",
         [("0000111122223333", "account")], ["account"], "account",
         "16-digit account number"),
    case("phi-067", "ACH: 987654321, bank code 021000021.",
         [("987654321", "account"), ("021000021", "account")],
         ["account"], "account",
         "ACH account + bank code"),
    case("phi-068", "Credit card 4111111111111111 on file.",
         [("4111111111111111", "account")], ["account"], "account",
         "Credit card number — functionally an account number"),
]

# ── Category 11: Certificate/license numbers ─────────────────────
cases += [
    case("phi-069", "My driver's license is D1234567.",
         [("D1234567", "license")], ["license"], "license",
         "Driver license"),
    case("phi-070", "DL number: S-5551234.",
         [("S-5551234", "license")], ["license"], "license",
         "DL with state prefix"),
    case("phi-071", "My medical license is MD-12345.",
         [("MD-12345", "license")], ["license"], "license",
         "Professional license"),
    case("phi-072", "Pharmacist license number PHM-987654.",
         [("PHM-987654", "license")], ["license"], "license",
         "Pharmacist license"),
    case("phi-073", "License: A123456789012.",
         [("A123456789012", "license")], ["license"], "license",
         "Long license number"),
    case("phi-074", "My RN license is RN-555123.",
         [("RN-555123", "license")], ["license"], "license",
         "Nursing license"),
]

# ── Category 12: Vehicle identifiers ──────────────────────────────
cases += [
    case("phi-075", "My car's VIN is 1HGBH41JXMN109283.",
         [("1HGBH41JXMN109283", "vehicle")], ["vehicle"], "vehicle",
         "17-character VIN"),
    case("phi-076", "License plate ABC-1234 (Massachusetts).",
         [("ABC-1234", "vehicle")], ["vehicle"], "vehicle",
         "License plate"),
    case("phi-077", "My truck's serial number is 5TFLF4X7AFX123456.",
         [("5TFLF4X7AFX123456", "vehicle")], ["vehicle"], "vehicle",
         "Truck serial"),
    case("phi-078", "Plate number 7ABC123.",
         [("7ABC123", "vehicle")], ["vehicle"], "vehicle",
         "Plate without dashes"),
    case("phi-079", "VIN 2T1BURHE0JC012345 was in the accident report.",
         [("2T1BURHE0JC012345", "vehicle")], ["vehicle"], "vehicle",
         "VIN mid-sentence"),
    case("phi-080", "Truck ID F-150-XL-555123.",
         [("F-150-XL-555123", "vehicle")], ["vehicle"], "vehicle",
         "Truck model + ID"),
]

# ── Category 13: Device identifiers and serial numbers ────────────
cases += [
    case("phi-081", "I use my Dexcom G7 to track sugar.",
         [("Dexcom G7", "device")], ["device"], "device",
         "Spec §4.1 edge case: device identifier referenced casually", edge=True),
    case("phi-082", "My Omnipod 5 stopped working yesterday.",
         [("Omnipod 5", "device")], ["device"], "device",
         "Insulin pump name"),
    case("phi-083", "Freestyle Libre 14 day sensor.",
         [("Freestyle Libre", "device")], ["device"], "device",
         "Continuous glucose monitor"),
    case("phi-084", "Medtronic 670G is my insulin pump.",
         [("Medtronic 670G", "device")], ["device"], "device",
         "Insulin pump with model number"),
    case("phi-085", "My pump's serial is SN-DM1234567.",
         [("SN-DM1234567", "device")], ["device"], "device",
         "Device serial number"),
    case("phi-086", "Tandem t:slim X2 insulin pump.",
         [("Tandem t:slim X2", "device")], ["device"], "device",
         "Specific pump model"),
    case("phi-087", "My CGM device ID DEV-ABC123DEF456.",
         [("DEV-ABC123DEF456", "device")], ["device"], "device",
         "Generic device ID"),
    case("phi-088", "I just got the new Libre 3 sensor.",
         [("Libre 3", "device")], ["device"], "device",
         "Newer device model"),
]

# ── Category 14: URLs ─────────────────────────────────────────────
cases += [
    case("phi-089", "My patient portal is at https://mychart.example.org/patient/123.",
         [("https://mychart.example.org/patient/123", "url")], ["url"], "url",
         "Patient portal URL with patient ID in path"),
    case("phi-090", "Visit www.carescaffold.example to learn more.",
         [("www.carescaffold.example", "url")], ["url"], "url",
         "Bare www URL"),
    case("phi-091", "I found info at https://diabetes.org about my meds.",
         [("https://diabetes.org", "url")], ["url"], "url",
         "URL mid-sentence"),
    case("phi-092", "My profile: carescaffold.example/users/johnsmith",
         [("carescaffold.example/users/johnsmith", "url")], ["url"], "url",
         "URL with user path (identifies the user)"),
    case("phi-093", "Lab results: http://labs.example.com/results?id=ABC123",
         [("http://labs.example.com/results?id=ABC123", "url")], ["url"], "url",
         "URL with query param containing patient ID"),
    case("phi-094", "Photo of my last meal: instagram.com/p/ABC123XYZ",
         [("instagram.com/p/ABC123XYZ", "url")], ["url"], "url",
         "Social media URL — could identify the patient"),
]

# ── Category 15: IP addresses ─────────────────────────────────────
cases += [
    case("phi-095", "My home IP is 192.168.1.100 if that helps with telehealth.",
         [("192.168.1.100", "ip")], ["ip"], "ip",
         "IPv4 address"),
    case("phi-096", "Server IP 10.0.0.5 hosts our records.",
         [("10.0.0.5", "ip")], ["ip"], "ip",
         "Server IP mid-sentence"),
    case("phi-097", "My IP is 172.16.254.1 right now.",
         [("172.16.254.1", "ip")], ["ip"], "ip",
         "IPv4 address"),
    case("phi-098", "IPv6: 2001:0db8:85a3:0000:0000:8a2e:0370:7334",
         [("2001:0db8:85a3:0000:0000:8a2e:0370:7334", "ip")],
         ["ip"], "ip",
         "IPv6 address"),
    case("phi-099", "Connection from 8.8.8.8 logged at 3am.",
         [("8.8.8.8", "ip")], ["ip"], "ip",
         "Public IPv4"),
    case("phi-100", "My doctor's office network is at 207.123.45.67.",
         [("207.123.45.67", "ip")], ["ip"], "ip",
         "IPv4 mid-sentence"),
]

# ── Category 16: Biometric identifiers ────────────────────────────
cases += [
    case("phi-101", "My fingerprint is on file for patient verification.",
         [("fingerprint", "biometric")], ["biometric"], "biometric",
         "Fingerprint reference"),
    case("phi-102", "The clinic uses voiceprint ID for phone check-in.",
         [("voiceprint", "biometric")], ["biometric"], "biometric",
         "Voiceprint reference"),
    case("phi-103", "Retinal scan was used for ID at the ER.",
         [("Retinal scan", "biometric")], ["biometric"], "biometric",
         "Retinal scan"),
    case("phi-104", "My faceprint is used to unlock the patient portal.",
         [("faceprint", "biometric")], ["biometric"], "biometric",
         "Faceprint"),
    case("phi-105", "They took my palm print at registration.",
         [("palm print", "biometric")], ["biometric"], "biometric",
         "Palm print"),
    case("phi-106", "Iris scan failed so they used a fingerprint instead.",
         [("Iris scan", "biometric"), ("fingerprint", "biometric")],
         ["biometric"], "biometric",
         "Two biometrics in one message"),
]

# ── Category 17: Full-face photographs and comparable images ─────
cases += [
    case("phi-107", "My photo is at /images/patients/12345.jpg.",
         [("/images/patients/12345.jpg", "photo")], ["photo"], "photo",
         "Photo file path with patient ID"),
    case("phi-108", "See my profile picture at https://example.com/me.jpg.",
         [("https://example.com/me.jpg", "photo")], ["photo"], "photo",
         "Photo URL"),
    case("phi-109", "Full face photo: img/patient_555.png",
         [("img/patient_555.png", "photo")], ["photo"], "photo",
         "Photo file reference"),
    case("phi-110", "I uploaded my photo to /uploads/selfie_abc123.jpg.",
         [("/uploads/selfie_abc123.jpg", "photo")], ["photo"], "photo",
         "Selfie upload"),
    case("phi-111", "My headshot is at photos.example.com/user/jsmith",
         [("photos.example.com/user/jsmith", "photo")], ["photo"], "photo",
         "Photo URL with username"),
    case("phi-112", "Scan of my ID photo: scans/dl_front_555123.png",
         [("scans/dl_front_555123.png", "photo")], ["photo"], "photo",
         "Photo of identification"),
]

# ── Category 18: Any other unique identifying number/code ────────
cases += [
    case("phi-113", "My patient code is PAT-2024-XYZ.",
         [("PAT-2024-XYZ", "unique_code")], ["unique_code"], "unique_code",
         "Patient-specific code"),
    case("phi-114", "Clinic ID CLN-555-1234.",
         [("CLN-555-1234", "unique_code")], ["unique_code"], "unique_code",
         "Clinic-assigned ID"),
    case("phi-115", "My case number is CASE2024001.",
         [("CASE2024001", "unique_code")], ["unique_code"], "unique_code",
         "Case number"),
    case("phi-116", "I'm in study STU-555123.",
         [("STU-555123", "unique_code")], ["unique_code"], "unique_code",
         "Clinical study ID"),
    case("phi-117", "Tracking ID TRK-ABC123XYZ.",
         [("TRK-ABC123XYZ", "unique_code")], ["unique_code"], "unique_code",
         "Tracking ID"),
    case("phi-118", "Appointment confirmation: APT-2024-09-15-1234.",
         [("APT-2024-09-15-1234", "unique_code")], ["unique_code"], "unique_code",
         "Appointment ID"),
    case("phi-119", "Specimen ID SP-098765.",
         [("SP-098765", "unique_code")], ["unique_code"], "unique_code",
         "Lab specimen ID"),
    case("phi-120", "Token: TK-555-ABC-123.",
         [("TK-555-ABC-123", "unique_code")], ["unique_code"], "unique_code",
         "Auth token (also identifies patient in some systems)"),
]

# ── Multi-category edge cases (spec §4.1: identifiers patients use for each other) ──
cases += [
    case("phi-121",
         "Hi, my name is Robert Chen (DOB 03/15/1958), and I'd like to discuss my test results. "
         "My friend Alice Wang (she's also T2D, lives in Boston, phone 555-123-4567) recommended this clinic.",
         [("Robert Chen", "names"), ("03/15/1958", "dates"), ("Alice Wang", "names"),
          ("Boston", "geographic"), ("555-123-4567", "phone")],
         ["names", "dates", "geographic", "phone"], "names",
         "Multi-category: patient self-identifies + mentions peer with PHI",
         edge=True),
    case("phi-122",
         "Dr. Patel (patel@hospital.org) said my MRN is MRN-5551234 and to call (555) 987-6543 with questions.",
         [("Dr. Patel", "names"), ("patel@hospital.org", "email"),
          ("MRN-5551234", "mrn"), ("(555) 987-6543", "phone")],
         ["names", "email", "mrn", "phone"], "mrn",
         "Multi-category: doctor + email + MRN + phone in one sentence",
         edge=True),
    case("phi-123",
         "My diabetes nurse Jenny recommended I check my Dexcom G7 readings more often.",
         [("Jenny", "names"), ("Dexcom G7", "device")],
         ["names", "device"], "device",
         "Spec §4.1 edge case: peer identifier (first name only) + casual device reference",
         edge=True),
    case("phi-124",
         "I've been using the Freestyle Libre since August 12th — my friend Dave got one too.",
         [("Freestyle Libre", "device"), ("August 12th", "dates"), ("Dave", "names")],
         ["device", "dates", "names"], "device",
         "Device + date + peer name",
         edge=True),
    case("phi-125",
         "Traveling to visit my mom in Jacksonville, FL next week — worried about my Omnipod 5 going through airport security.",
         [("Jacksonville", "geographic"), ("Omnipod 5", "device")],
         ["geographic", "device"], "device",
         "Geographic + device",
         edge=True),
    case("phi-126",
         "Email me at test@example.com or call my cell at 5551234567 — my address is 456 Pine St, Seattle.",
         [("test@example.com", "email"), ("5551234567", "phone"),
          ("456 Pine St", "geographic"), ("Seattle", "geographic")],
         ["email", "phone", "geographic"], "email",
         "Email + phone + address in one message",
         edge=True),
    case("phi-127",
         "Hospital stay from 2024-01-15 to 2024-01-22, MRN 88776655, in room 412 at General Hospital.",
         [("2024-01-15", "dates"), ("2024-01-22", "dates"),
          ("88776655", "mrn"), ("General Hospital", "names")],
         ["dates", "mrn", "names"], "mrn",
         "Multiple dates + MRN + hospital name",
         edge=True),
    case("phi-128",
         "I uploaded a photo of my last lab report to mychart.example.org/records/12345 — it has my SSN 123-45-6789 on it.",
         [("mychart.example.org/records/12345", "url"),
          ("123-45-6789", "ssn")],
         ["url", "ssn"], "ssn",
         "URL with patient ID + SSN in same message — high-risk combo",
         edge=True),
    case("phi-129",
         "My CGM is the Medtronic 670G (serial SN-MDT-123456), Medicare ID 1AB2CD3EF4.",
         [("Medtronic 670G", "device"), ("SN-MDT-123456", "device"),
          ("1AB2CD3EF4", "beneficiary")],
         ["device", "beneficiary"], "device",
         "Device + device serial + insurance ID",
         edge=True),
    case("phi-130",
         "Born 1958-04-12, hospital badge ID EMP-9900 at Mercy West Hospital.",
         [("1958-04-12", "dates"), ("EMP-9900", "unique_code"),
          ("Mercy West Hospital", "names")],
         ["dates", "unique_code", "names"], "unique_code",
         "Date + employee badge + hospital name",
         edge=True),
]

# ── More edge cases (the spec calls out: misspelled names, addresses mid-sentence) ──
cases += [
    case("phi-131",
         "I think my doctor's name is Jonh Smith (with the missing h, that's how he spells it).",
         [("Jonh Smith", "names")], ["names"], "names",
         "Spec §4.1 edge case: misspelled name", edge=True),
    case("phi-132",
         "I work at 200 Park Avenue, Suite 400, in NYC.",
         [("200 Park Avenue", "geographic"), ("NYC", "geographic")],
         ["geographic"], "geographic",
         "Address mid-sentence + city abbreviation",
         edge=True),
    case("phi-133",
         "My buddy 'Tiny' (real name Anthony) has T2D too.",
         [("'Tiny'", "names"), ("Anthony", "names")],
         ["names"], "names",
         "Spec §4.1 edge case: nickname with quotes",
         edge=True),
    case("phi-134",
         "I saw the doc — Thompson, I think — last Friday.",
         [("Thompson", "names"), ("last Friday", "dates")],
         ["names", "dates"], "names",
         "Spec §4.1 edge case: doctor last name referenced mid-sentence with em-dashes",
         edge=True),
    case("phi-135",
         "My daughter Kavya is 11 and asked about my diabetes.",
         [("Kavya", "names"), ("11", "dates")],
         ["names", "dates"], "names",
         "Family member name + age under 89 (age itself not PHI under 89, but the name is)",
         edge=True),
    case("phi-136",
         "I think my friend's MRN is something like 445566 but I'm not sure.",
         [("445566", "mrn")], ["mrn"], "mrn",
         "Spec §4.1 edge case: peer identifier + uncertain reference",
         edge=True),
    case("phi-137",
         "I had my last A1C around mid-March.",
         [("mid-March", "dates")], ["dates"], "dates",
         "Imprecise date reference (mid-March)",
         edge=True),
    case("phi-138",
         "My doctor's first name is Fawzia, she's at the Westside clinic.",
         [("Fawzia", "names"), ("Westside", "geographic")],
         ["names", "geographic"], "names",
         "Spec §4.1 edge case: first name only + clinic name (geographic identifier)",
         edge=True),
]

# ── A few more to push past 150 (and add variety) ──
cases += [
    case("phi-139", "I parked my car (license XYZ-9876) outside the clinic.",
         [("XYZ-9876", "vehicle")], ["vehicle"], "vehicle",
         "License plate mid-sentence"),
    case("phi-140", "Email: firstname.lastname@example.org, alt: flast@example.com",
         [("firstname.lastname@example.org", "email"), ("flast@example.com", "email")],
         ["email"], "email", "Two emails in one message"),
    case("phi-141", "My Apple Watch serial is SN-AW1234567.",
         [("SN-AW1234567", "device")], ["device"], "device",
         "Wearable device serial"),
    case("phi-142", "I'm calling from 555-555-0199 at the nurse's station.",
         [("555-555-0199", "phone")], ["phone"], "phone",
         "Phone in context of calling from a location"),
    case("phi-143", "Two days after I started metformin, I felt nauseous.",
         [("Two days after I started metformin", "dates")], ["dates"], "dates",
         "Relative date referencing medication start", edge=True),
    case("phi-144", "My home ZIP is 94110, work ZIP is 94105.",
         [("94110", "geographic"), ("94105", "geographic")],
         ["geographic"], "geographic",
         "Two ZIP codes"),
    case("phi-145", "My clinic's patient portal is patient.mercyhealth.example.",
         [("patient.mercyhealth.example", "url")], ["url"], "url",
         "Patient portal URL (no https prefix)"),
    case("phi-146", "I have a Medtronic MiniMed 770G pump.",
         [("Medtronic MiniMed 770G", "device")], ["device"], "device",
         "Specific pump model with version"),
    case("phi-147", "Dr. Patel's nurse practitioner is Sangita Reddy.",
         [("Dr. Patel", "names"), ("Sangita Reddy", "names")],
         ["names"], "names",
         "Two clinician names in one message"),
    case("phi-148", "Last 4 of my SSN are 1234.",
         [("1234", "ssn")], ["ssn"], "ssn",
         "Last 4 of SSN — often not PHI alone, but flagged as edge case", edge=True),
    case("phi-149", "I live in apartment 4B at 1234 Elm Street, Apt 4B, Springfield.",
         [("1234 Elm Street", "geographic"), ("Apt 4B", "geographic"),
          ("Springfield", "geographic")],
         ["geographic"], "geographic",
         "Address with apartment number"),
    case("phi-150", "I was at the clinic on 2024-09-15 from 9am to 11am.",
         [("2024-09-15", "dates"), ("9am", "dates"), ("11am", "dates")],
         ["dates"], "dates",
         "Date + times"),
    case("phi-151", "My doctor's office IP is 192.168.50.50 and SSID is ClinicWiFi.",
         [("192.168.50.50", "ip")], ["ip"], "ip",
         "IP address + WiFi name (SSID not PHI but provides context)"),
    case("phi-152", "I have a Dexcom G6 (older model) and my friend has the G7.",
         [("Dexcom G6", "device"), ("G7", "device")],
         ["device"], "device",
         "Two device versions in one message — G7 alone may be hard to catch without context",
         edge=True),
]


def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    print(f"Writing {len(cases)} cases to {OUTPUT}")
    # Sanity check: every span is actually findable in its input
    for c in cases:
        for span in c["expected_spans"]:
            if span["span"] not in c["input"]:
                raise ValueError(
                    f"Case {c['id']}: span {span['span']!r} not in input {c['input']!r}"
                )
        # Sanity: start/end correct
        for span in c["expected_spans"]:
            if c["input"][span["start"]:span["end"]] != span["span"]:
                raise ValueError(
                    f"Case {c['id']}: span offsets wrong for {span['span']!r}"
                )
    # Count categories
    from collections import Counter
    cat_counts = Counter(c["category"] for c in cases)
    print(f"\nCategory coverage:")
    for cat, count in sorted(cat_counts.items()):
        print(f"  {cat}: {count}")
    print(f"\nEdge cases (spec §4.1): {sum(1 for c in cases if c['edge_case'])}")
    print(f"Total: {len(cases)}")

    with OUTPUT.open("w") as f:
        for c in cases:
            f.write(json.dumps(c) + "\n")
    print(f"\n✓ Wrote {len(cases)} cases to {OUTPUT}")


if __name__ == "__main__":
    main()
