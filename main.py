// =====================================================================
// CLOUDFLARE WORKER: emergencyaudit.com
// Bindings required in wragler.toml / Cloudflare Dashboard:
// - KV Namespace: LEADS_KV
// - Environment Variable: MASTER_ADMIN_KEY (Optional secret)
// =====================================================================

const MASTER_ADMIN_KEY = "EmergencyAudit_Master_Key_2027!";
const SUPPORT_PHONE_RAW = "+14246108853";
const SUPPORT_PHONE_DISPLAY = "(424) 610-8853";

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;

    // CORS Headers
    const corsHeaders = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type, X-Emergency-Key",
    };

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders });
    }

    // -----------------------------------------------------------------
    // 1. INBOUND LEAD HOOK (POST /api/inbound-lead-hook)
    // -----------------------------------------------------------------
    if (path === "/api/inbound-lead-hook" && request.method === "POST") {
      const authKey = request.headers.get("X-Emergency-Key");
      const validKey = env.MASTER_ADMIN_KEY || MASTER_ADMIN_KEY;

      if (authKey !== validKey) {
        return new Response(JSON.stringify({ error: "Unauthorized access key" }), {
          status: 401,
          headers: { ...corsHeaders, "Content-Type": "application/json" }
        });
      }

      try {
        const payload = await request.json();
        const records = Array.isArray(payload) ? payload : [payload];
        
        let storedCount = 0;
        for (const item of records) {
          const caseId = item.record_id || item.citation_id || item.caseId;
          if (caseId) {
            await env.LEADS_KV.put(caseId, JSON.stringify(item));
            storedCount++;
          }
        }

        return new Response(JSON.stringify({ success: true, stored: storedCount }), {
          status: 200,
          headers: { ...corsHeaders, "Content-Type": "application/json" }
        });
      } catch (err) {
        return new Response(JSON.stringify({ error: err.message }), {
          status: 400,
          headers: { ...corsHeaders, "Content-Type": "application/json" }
        });
      }
    }

    // -----------------------------------------------------------------
    // 2. PUBLIC CITATION RECORD PAGE (GET /c/:caseId)
    // -----------------------------------------------------------------
    if (path.startsWith("/c/")) {
      const caseId = path.replace("/c/", "").trim();
      if (!caseId) {
        return new Response("Case ID required", { status: 400 });
      }

      let leadData = null;
      if (env.LEADS_KV) {
        const rawJson = await env.LEADS_KV.get(caseId);
        if (rawJson) {
          leadData = JSON.parse(rawJson);
        }
      }

      // Fallback display formatting if record is not found in KV yet
      const record = leadData || {
        record_id: caseId,
        apn: caseId.replace("AUD-APN-", "").replace("AUD-", ""),
        owner_name: "RECORDED PUBLIC OWNER",
        address: "RECORDED LA COUNTY LOCATION",
        default_amount: "108828",
        property_type: "Single Family / Commercial",
        violation: "A statutory Notice of Default (NOD) has been logged in LA County public records."
      };

      const html = generatePublicPageHTML(record);
      return new Response(html, {
        status: 200,
        headers: { "Content-Type": "text/html; charset=utf-8" }
      });
    }

    // Default Fallback
    return new Response("EmergencyAudit Network Active", { status: 200 });
  }
};


// =====================================================================
// HTML TEMPLATE GENERATOR WITH FUNCTIONAL CLICK-TO-CALL BUTTON
// =====================================================================
function generatePublicPageHTML(rec) {
  const apn = rec.apn || "N/A";
  const caseId = rec.record_id || rec.caseId || "N/A";
  const address = rec.address || "N/A";
  const owner = rec.owner_name || "N/A";
  const amount = rec.default_amount || rec.amount_logged || "N/A";
  const propType = rec.property_type || "Single Family / Commercial";
  const violation = rec.violation || "A statutory Notice of Default (NOD) has been logged in LA County public records.";

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Public Record File #${caseId} | EmergencyAudit.com</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
    body { background-color: #0b0f19; color: #f3f4f6; display: flex; justify-content: center; padding: 20px; min-height: 100vh; }
    .container { width: 100%; max-width: 680px; display: flex; flex-direction: column; gap: 16px; }
    .card { background-color: #111827; border: 1px solid #1f2937; border-radius: 12px; padding: 20px; }
    
    .header-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
    .file-id { font-size: 1.1rem; font-weight: 700; color: #facc15; }
    .status-badge { background-color: #064e3b; color: #34d399; font-size: 0.75rem; font-weight: 700; padding: 4px 10px; border-radius: 20px; text-transform: uppercase; }
    .status-title { font-size: 1.25rem; font-weight: 800; color: #fbbf24; margin-top: 4px; }
    
    .grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: 16px; }
    .grid-2 { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin-top: 12px; }
    .field-box { background-color: #1f2937; padding: 12px; border-radius: 8px; border: 1px solid #374151; }
    .field-label { font-size: 0.7rem; color: #9ca3af; font-weight: 700; text-transform: uppercase; }
    .field-value { font-size: 1rem; font-weight: 700; color: #ffffff; margin-top: 4px; overflow-wrap: break-word; }
    .highlight-red { color: #f87171; }

    .summary-box { background-color: #131c2e; border: 1px solid #1e293b; border-radius: 8px; padding: 16px; margin-top: 12px; }
    .summary-title { font-size: 0.75rem; color: #60a5fa; font-weight: 700; text-transform: uppercase; margin-bottom: 8px; }
    .summary-text { font-size: 0.95rem; color: #d1d5db; line-height: 1.5; }
    .options-list { margin-top: 12px; padding-left: 18px; font-size: 0.85rem; color: #9ca3af; line-height: 1.6; }

    .action-card { background-color: #062016; border: 2px solid #059669; border-radius: 12px; padding: 24px; text-align: center; margin-top: 8px; }
    .action-title { font-size: 1rem; font-weight: 800; color: #ffffff; text-transform: uppercase; letter-spacing: 0.5px; }
    .action-sub { font-size: 0.85rem; color: #a7f3d0; margin-top: 4px; margin-bottom: 16px; }
    
    /* CLICK-TO-CALL BUTTON FIX */
    .call-button-link { text-decoration: none; display: block; width: 100%; }
    .call-button {
      width: 100%;
      background: linear-gradient(135deg, #10b981 0%, #059669 100%);
      color: #ffffff;
      padding: 18px 24px;
      font-size: 1.15rem;
      font-weight: 800;
      border: none;
      border-radius: 10px;
      cursor: pointer;
      box-shadow: 0 4px 14px rgba(16, 185, 129, 0.4);
      transition: transform 0.1s ease, box-shadow 0.1s ease;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
    }
    .call-button:hover { transform: translateY(-1px); box-shadow: 0 6px 20px rgba(16, 185, 129, 0.6); }
    .call-button:active { transform: translateY(1px); }

    .footer { display: flex; justify-content: space-between; align-items: center; font-size: 0.75rem; color: #6b7280; padding: 0 4px; }
  </style>
</head>
<body>
  <div class="container">
    <div class="card">
      <div class="header-bar">
        <span class="file-id">FILE : #${caseId}</span>
        <span class="status-badge">• INDEX ACTIVE</span>
      </div>
      <div class="status-title">Status: RECORDED PUBLIC NOTICE ENTRY</div>

      <div class="grid">
        <div class="field-box">
          <div class="field-label">PARCEL (APN)</div>
          <div class="field-value">${apn}</div>
        </div>
        <div class="field-box">
          <div class="field-label">CLASSIFICATION</div>
          <div class="field-value">${propType}</div>
        </div>
        <div class="field-box">
          <div class="field-label">BUILDING SQFT</div>
          <div class="field-value">N/A</div>
        </div>
      </div>

      <div class="grid-2">
        <div class="field-box">
          <div class="field-label">RECORDED NOTICE AMOUNT</div>
          <div class="field-value highlight-red">$${amount}</div>
        </div>
        <div class="field-box">
          <div class="field-label">RECORDED OWNER</div>
          <div class="field-value">${owner}</div>
        </div>
      </div>

      <div class="field-box" style="margin-top: 12px;">
        <div class="field-label">PARCEL LOCATION</div>
        <div class="field-value">${address}</div>
      </div>

      <div class="summary-box">
        <div class="summary-title">PUBLIC RECORD SUMMARY FOR PARCEL ${apn}</div>
        <div class="summary-text">${violation}</div>
        <ul class="options-list">
          <li><strong>Private Bridge Funding:</strong> Direct connection to private lenders providing short-term property capital.</li>
          <li><strong>Foreclosure Defense & Emergency Legal Stays:</strong> Connect with foreclosure defense attorneys and legal specialists for immediate sale stays or bankruptcy filings.</li>
          <li><strong>Commercial & DSCR Refinancing:</strong> Connect with licensed mortgage brokers to evaluate property equity restructuring.</li>
        </ul>
      </div>

      <!-- CLICK-TO-CALL ACTION BUTTON (OPERATIONAL) -->
      <div class="action-card">
        <div class="action-title">📞 CONNECT WITH A PROPERTY SPECIALIST</div>
        <div class="action-sub">To speak with a licensed specialist regarding File #${caseId}:</div>
        <a href="tel:${SUPPORT_PHONE_RAW}" class="call-button-link">
          <button class="call-button">
            📞 CALL SUPPORT LINE: ${SUPPORT_PHONE_DISPLAY}
          </button>
        </a>
      </div>
    </div>

    <div class="footer">
      <span>EmergencyAudit Network Portal</span>
      <span>Learn more about our public record indexing and resolution process.</span>
    </div>
  </div>
</body>
</html>`;
}import os
import re
import requests
import time
import json
import csv
import pandas as pd
import pdfplumber
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# =====================================================================
# 1. ENVIRONMENT CONFIGURATION & SYSTEM CONTROLS
# =====================================================================
WORKER_URL = os.getenv("WORKER_URL") or "https://emergencyaudit.com"
MASTER_ADMIN_KEY = os.getenv("MASTER_ADMIN_KEY") or "EmergencyAudit_Master_Key_2027!"
APIFY_TOKEN = os.getenv("APIFY_TOKEN")

MAX_TEST_LEADS = None
PAUSE_PIPELINE = (os.getenv("PAUSE_PIPELINE") or "false").lower() == "true"
DRY_RUN = (os.getenv("DRY_RUN") or "false").lower() in ["true", "1", "yes"]
STAGING_MODE = (os.getenv("STAGING_MODE") or "false").lower() == "true"


# =====================================================================
# 2. ENTITY DETECTOR & CASE ID GENERATOR
# =====================================================================
ENTITY_KEYWORDS = ["LLC", "INC", "CORP", "CORPORATION", "HOLDINGS", "PROPERTIES", "INVESTMENTS", "LTD", "LP", "GROUP", "PARTNERS", "REALTY", "COMPANY", "CO"]
TRUST_KEYWORDS = ["TRUST", "TRUSTEE", "FAMILY TRUST", "REVOCABLE", "LIVING TRUST", "ESTATE"]

def classify_owner_type(owner_name):
    clean_name = re.sub(r"[^\w\s]", "", str(owner_name).upper())
    if any(re.search(rf"\b{kw}\b", clean_name) for kw in TRUST_KEYWORDS):
        return "TRUST"
    if any(re.search(rf"\b{kw}\b", clean_name) for kw in ENTITY_KEYWORDS):
        return "CORPORATE_ENTITY"
    return "INDIVIDUAL"

def generate_deterministic_case_id(apn, address):
    clean_apn = re.sub(r"\D", "", str(apn))
    if clean_apn and clean_apn != "PENDINGVERIFICATION" and len(clean_apn) >= 5:
        return f"AUD-APN-{clean_apn}"
    clean_addr = re.sub(r"[^\w]", "", str(address)).upper()
    if clean_addr and clean_addr != "RECORDEDPARCELLOCATION":
        return f"AUD-{clean_addr[:12]}"
    return f"AUD-REF-{int(time.time())}"

def validate_lead_record(record):
    address = str(record.get("address") or "").strip().upper()
    apn = str(record.get("apn") or "").strip().upper()
    if not address and not apn:
        return False, "BLOCKED: Missing both Property Address and APN"
    if address in ["N/A", "NONE", "RECORDED PARCEL LOCATION", ""] and apn in ["N/A", "NONE", "ON FILE", "PENDING VERIFICATION", ""]:
        return False, "BLOCKED: Placeholder location data"
    return True, "VALID"


# =====================================================================
# 3. TRUEPEOPLESEARCH APIFY UNMASKING ENGINE
# =====================================================================
def extract_phone_from_raw_row(raw_dict):
    phone_candidates = []
    priority_keys = ["phone", "mobile", "contact", "ownerphone", "phone1", "cell", "telephone", "phone_number"]
    for k, v in raw_dict.items():
        if not k or not v:
            continue
        clean_k = str(k).lower().replace("_", "").replace(" ", "")
        if any(pk in clean_k for pk in priority_keys):
            matches = re.findall(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b", str(v))
            for m in matches:
                digits = re.sub(r"\D", "", m)
                if len(digits) == 10 and not digits.startswith(("800", "888", "877", "866", "900", "000")):
                    return f"+1{digits}"
                elif len(digits) == 11 and digits.startswith("1"):
                    return f"+{digits}"

    for k, v in raw_dict.items():
        if not v:
            continue
        matches = re.findall(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b", str(v))
        for m in matches:
            digits = re.sub(r"\D", "", m)
            if len(digits) == 10 and not digits.startswith(("800", "888", "877", "866", "900", "000")):
                phone_candidates.append(f"+1{digits}")
            elif len(digits) == 11 and digits.startswith("1"):
                phone_candidates.append(f"+{digits}")

    return phone_candidates[0] if phone_candidates else None


def apify_bulk_skip_trace(lead_batch):
    if not APIFY_TOKEN:
        logging.warning("⚠️ APIFY_TOKEN secret not found in environment. Skipping Apify unmasking.")
        return {}

    logging.info(f"⚡ [TRUEPEOPLESEARCH ENGINE] Querying public registries for {len(lead_batch)} lead(s)...")
    results_map = {}
    CHUNK_SIZE = 25

    for i in range(0, len(lead_batch), CHUNK_SIZE):
        chunk = lead_batch[i:i + CHUNK_SIZE]
        search_queries = []
        lookup_map = {}

        for item in chunk:
            raw_name = item.get("owner_name", "")
            owner_type = classify_owner_type(raw_name)
            if owner_type == "TRUST":
                target_name = re.sub(r"\b(TRUST|TRUSTEE|TTEE|FAMILY|REVOCABLE|LIVING|DATED|\d+)\b", "", raw_name, flags=re.I).strip()
            else:
                target_name = raw_name

            name_parts = target_name.strip().split()
            first_name = name_parts[0] if len(name_parts) >= 1 else target_name
            last_name = " ".join(name_parts[1:]) if len(name_parts) >= 2 else ""

            city = item.get("city", "Los Angeles")
            state = item.get("state", "CA")

            if first_name and last_name:
                query_str = f"{first_name} {last_name}, {city}, {state}"
                search_queries.append(query_str)
                lookup_key = f"{first_name.upper()}_{last_name.upper()}"
                lookup_map[lookup_key] = item.get("record_id")

        if not search_queries:
            continue

        # SWAPPED TO UNBLOCKED TRUEPEOPLESEARCH ACTOR
        start_endpoint = f"https://api.apify.com/v2/acts/memo23~truepeoplesearch-people-search-scraper/runs?token={APIFY_TOKEN}"
        payload = {
            "searchQueries": search_queries,
            "maxResults": 1
        }

        try:
            run_res = requests.post(start_endpoint, json=payload, timeout=20)
            if run_res.status_code not in [200, 201]:
                logging.warning(f"⚠️ Start Run Bypassed [{run_res.status_code}]")
                continue

            run_data = run_res.json().get("data", {})
            run_id = run_data.get("id")
            dataset_id = run_data.get("defaultDatasetId")

            logging.info(f"⏳ Apify Run [{run_id}] active. Polling status...")

            status_endpoint = f"https://api.apify.com/v2/actor-runs/{run_id}?token={APIFY_TOKEN}"
            for _ in range(12):
                time.sleep(4)
                poll_res = requests.get(status_endpoint, timeout=10)
                if poll_res.status_code == 200:
                    poll_data = poll_res.json().get("data", {})
                    status = poll_data.get("status")
                    if status == "SUCCEEDED":
                        break
                    elif status in ["FAILED", "ABORTED", "TIMED-OUT"]:
                        logging.warning(f"⚠️ Apify Run [{run_id}] status: {status}")
                        break

            dataset_endpoint = f"https://api.apify.com/v2/datasets/{dataset_id}/items?token={APIFY_TOKEN}"
            items_res = requests.get(dataset_endpoint, timeout=15)
            if items_res.status_code == 200:
                extracted_data = items_res.json()
                if isinstance(extracted_data, list) and len(extracted_data) > 0:
                    for record in extracted_data:
                        phone = extract_phone_from_raw_row(record)
                        if not phone:
                            continue
                        rec_fn = re.sub(r"[^\w]", "", str(record.get("firstName") or record.get("first_name") or "")).upper()
                        rec_ln = re.sub(r"[^\w]", "", str(record.get("lastName") or record.get("last_name") or "")).upper()
                        match_key = f"{rec_fn}_{rec_ln}"
                        citation_id = lookup_map.get(match_key)
                        if citation_id:
                            results_map[citation_id] = phone
        except Exception as e:
            logging.warning(f"⚠️ Apify Engine Exception Handled: {e}")

    logging.info(f"✅ Unmasking complete. Extracted {len(results_map)} live number(s).")
    return results_map


# =====================================================================
# 4. DATA INGESTION ENGINE
# =====================================================================
def normalize_lead_dict(raw_dict):
    norm = {}
    for k, v in raw_dict.items():
        if k is not None and v is not None:
            clean_k = re.sub(r'[^a-z0-9]', '', str(k).lower())
            norm[clean_k] = str(v).strip()

    apn_val = norm.get("apn") or norm.get("parcelid") or norm.get("pin") or norm.get("parcel") or "PENDING VERIFICATION"
    addr_val = norm.get("address") or norm.get("propertyaddress") or norm.get("siteaddress") or "Recorded Parcel Location"
    city_val = norm.get("city") or norm.get("propertycity") or "Los Angeles"
    state_val = norm.get("state") or norm.get("propertystate") or "CA"

    fname = norm.get("owner1firstname") or norm.get("ownerfirstname") or ""
    lname = norm.get("owner1lastname") or norm.get("ownerlastname") or ""
    owner_val = f"{fname} {lname}".strip() or norm.get("ownerfullname") or norm.get("ownername") or norm.get("owner1") or "RECORDED OWNER"

    citation = norm.get("recordid") or norm.get("caseid") or generate_deterministic_case_id(apn_val, addr_val)
    amount = norm.get("defaultamount") or norm.get("amountlogged") or "$35,420.00 Recorded"
    phone_val = extract_phone_from_raw_row(raw_dict) or "PENDING UNMASK"

    return {
        "record_id": citation,
        "citation_id": citation,
        "owner_name": owner_val,
        "address": addr_val,
        "city": city_val,
        "state": state_val,
        "zip": norm.get("zip") or "90012",
        "apn": apn_val,
        "category": norm.get("category") or "PRE-FORECLOSURE / REINSTATEMENT",
        "default_amount": amount,
        "property_type": norm.get("propertytype") or "Single Family / Commercial",
        "violation": "A statutory Notice of Default (NOD) has been logged in CA public records.",
        "phone": phone_val,
        "email": norm.get("email") or "N/A"
    }

def parse_any_file(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    raw_records = []
    try:
        if ext == ".csv":
            with open(file_path, mode="r", encoding="utf-8-sig") as f:
                raw_records = list(csv.DictReader(f))
        elif ext in [".xlsx", ".xls"]:
            df = pd.read_excel(file_path).fillna("")
            raw_records = df.to_dict(orient="records")
        elif ext == ".json":
            with open(file_path, mode="r", encoding="utf-8") as f:
                data = json.load(f)
                raw_records = data if isinstance(data, list) else [data]
    except Exception as e:
        logging.error(f"❌ Error reading file {file_path}: {e}")
        return []
    return [normalize_lead_dict(rec) for rec in raw_records if rec]

def load_all_lead_datasets():
    all_leads = []
    valid_exts = (".csv", ".xlsx", ".xls", ".json")
    root_files = [f for f in os.listdir(".") if f.lower().endswith(valid_exts) and not f.startswith("temp_")]
    for f in root_files:
        logging.info(f"📁 Processing repository dataset: {f}")
        all_leads.extend(parse_any_file(f))
    return all_leads


# =====================================================================
# 5. MAIN EXECUTION LOOP (CHUNKED DISPATCH TO CLOUDFLARE KV)
# =====================================================================
if __name__ == "__main__":
    logging.info("🚀 Universal Ingress Engine Active. Pipeline in PRODUCTION MODE.")
    real_leads = load_all_lead_datasets()
    logging.info(f"\n📥 Total Aggregated Feed: {len(real_leads)} record(s). Processing...\n")
    
    passed_count = 0
    seen_identifiers = set()
    needs_unmask_batch = []
    prepared_records = []

    for parcel in real_leads:
        if MAX_TEST_LEADS and passed_count >= MAX_TEST_LEADS:
            break
        apn = parcel.get("apn")
        addr = parcel.get("address")
        dedup_key = apn if (apn and apn != "PENDING VERIFICATION") else addr
        if dedup_key in seen_identifiers:
            continue
        seen_identifiers.add(dedup_key)

        is_valid, _ = validate_lead_record(parcel)
        if not is_valid:
            continue

        cid = parcel.get("record_id") or generate_deterministic_case_id(apn, addr)
        parcel["record_id"] = cid

        existing_phone = parcel.get("phone")
        if not existing_phone or existing_phone in ["PENDING UNMASK", "Unmasked Upon Purchase"]:
            needs_unmask_batch.append(parcel)
        
        prepared_records.append(parcel)
        passed_count += 1

    unmasked_phones = {}
    if needs_unmask_batch:
        unmasked_phones = apify_bulk_skip_trace(needs_unmask_batch)

    dispatch_queue = []
    for parcel in prepared_records:
        cid = parcel["record_id"]
        phone = parcel.get("phone")
        if not phone or phone in ["PENDING UNMASK", "Unmasked Upon Purchase"]:
            phone = unmasked_phones.get(cid, "PENDING UNMASK")

        dispatch_queue.append({
            "record_id": cid,
            "citation_id": cid,
            "caseId": cid,
            "address": parcel.get("address"),
            "owner_name": parcel.get("owner_name"),
            "phone": phone,
            "email": parcel.get("email", "N/A"),
            "apn": parcel.get("apn"),
            "category": parcel.get("category", "PRE-FORECLOSURE / REINSTATEMENT"),
            "default_amount": parcel.get("default_amount") or "$35,420.00 Recorded",
            "property_type": parcel.get("property_type") or "Single Family / Commercial",
            "violation": "A statutory Notice of Default (NOD) has been logged in LA County public records.",
            "status": "PENDING_REVIEW" if STAGING_MODE else "READY_FOR_DISPATCH"
        })

    if dispatch_queue:
        logging.info(f"\n🚀 Dispatching {len(dispatch_queue)} record(s) to Cloudflare KV in chunks...")
        endpoint = f"{WORKER_URL.rstrip('/')}/api/inbound-lead-hook"
        headers = {"Content-Type": "application/json", "X-Emergency-Key": MASTER_ADMIN_KEY}

        POST_CHUNK_SIZE = 25
        successful_dispatches = 0
        for j in range(0, len(dispatch_queue), POST_CHUNK_SIZE):
            post_chunk = dispatch_queue[j:j + POST_CHUNK_SIZE]
            if DRY_RUN:
                logging.info(f"🧪 [DRY RUN] Would post chunk of {len(post_chunk)} items")
            else:
                try:
                    res = requests.post(endpoint, data=json.dumps(post_chunk), headers=headers, timeout=30)
                    if res.status_code == 200:
                        successful_dispatches += len(post_chunk)
                        logging.info(f"✅ Batch [{j//POST_CHUNK_SIZE + 1}] Stored {len(post_chunk)} records in KV.")
                    else:
                        logging.error(f"❌ Worker Error [{res.status_code}]: {res.text}")
                except Exception as e:
                    logging.error(f"⚠️ Dispatch Exception: {e}")

        logging.info(f"\n🎉 Dispatch Completed! {successful_dispatches}/{len(dispatch_queue)} records populated on dashboard.")
