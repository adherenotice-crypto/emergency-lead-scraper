import urllib.parse

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
        start_urls = []
        chunk_order_cids = []
        lookup_map = {}
        lookup_query_map = {}

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
                cid = item.get("record_id")
                chunk_order_cids.append(cid)
                
                # Direct search URL for TruePeopleSearch
                encoded_name = urllib.parse.quote(f"{first_name} {last_name}")
                encoded_loc = urllib.parse.quote(f"{city}, {state}")
                tps_url = f"https://www.truepeoplesearch.com/results?name={encoded_name}&citystatezip={encoded_loc}"
                start_urls.append({"url": tps_url})

                lookup_key = f"{first_name.upper()}_{last_name.upper()}"
                lookup_map[lookup_key] = cid
                lookup_query_map[query_str.upper()] = cid

        if not search_queries:
            continue

        start_endpoint = f"https://api.apify.com/v2/acts/memo23~truepeoplesearch-people-search-scraper/runs?token={APIFY_TOKEN}"
        
        # Multi-format payload to support all Apify TruePeopleSearch actor variants
        payload = {
            "searchQueries": search_queries,
            "queries": search_queries,
            "startUrls": start_urls,
            "maxResults": 1,
            "maxItems": len(search_queries)
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
                logging.info(f"📊 Apify Dataset returned {len(extracted_data)} item(s) for Batch [{i//CHUNK_SIZE + 1}].")
                
                if isinstance(extracted_data, list) and len(extracted_data) > 0:
                    for idx, record in enumerate(extracted_data):
                        phone = extract_phone_from_raw_row(record)
                        if not phone:
                            continue

                        matched_cid = None
                        
                        # 1. Match by Search Query / URL string
                        sq = str(record.get("searchQuery") or record.get("query") or record.get("url") or "").upper().strip()
                        if sq:
                            for q_key, cid in lookup_query_map.items():
                                if q_key in sq or sq in q_key:
                                    matched_cid = cid
                                    break

                        # 2. Match by Owner Name
                        if not matched_cid:
                            name_str = str(record.get("name") or record.get("fullName") or "").upper()
                            for q_key, cid in lookup_query_map.items():
                                owner_name = q_key.split(",")[0].strip()
                                if owner_name and owner_name in name_str:
                                    matched_cid = cid
                                    break

                        # 3. Fallback Match by Order Position
                        if not matched_cid and idx < len(chunk_order_cids):
                            matched_cid = chunk_order_cids[idx]

                        if matched_cid:
                            results_map[matched_cid] = phone
        except Exception as e:
            logging.warning(f"⚠️ Apify Engine Exception Handled: {e}")

    logging.info(f"✅ Unmasking complete. Extracted {len(results_map)} live number(s).")
    return results_map
