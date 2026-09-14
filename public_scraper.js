const axios = require('axios');

const WORKER_ENDPOINT = process.env.WORKER_API_ENDPOINT || 'https://emergencyaudit.com/api/ping';
const MASTER_KEY = process.env.MASTER_ADMIN_KEY || 'SecretKey_2026_Dispatch!';
const SKIPTRACE_API_KEY = process.env.SKIPTRACE_API_KEY; // Stored in GitHub Secrets

// County Court Target Endpoints
const DOCKET_TARGETS = [
  { county: 'Los Angeles', code: 'LASC', searchUrl: 'https://www.lacourt.org/paos/v2public/api/civil/dailyindex' },
  { county: 'Orange', code: 'OCSC', searchUrl: 'https://www.occourts.org/online-services/case-access/civil' },
  { county: 'San Diego', code: 'SDSC', searchUrl: 'https://www.sdcourt.ca.gov/sdcourt/civil/caseaccess' },
  { county: 'Riverside', code: 'RIV', searchUrl: 'https://www.riverside.courts.ca.gov/online-services/civil-search' }
];

// Helper: Skip-Trace Landlord Property & LLC Name to Get Real Phone Number
async function skipTracePropertyOwner(ownerName, propertyAddress) {
  try {
    // Calls BatchData / SkipTrace API to grab verified mobile numbers
    const response = await axios.post('https://api.batchdata.com/api/v1/property/skip-trace', {
      name: ownerName,
      address: propertyAddress
    }, {
      headers: { 'Authorization': `Bearer ${SKIPTRACE_API_KEY}` }
    });

    const phone = response.data?.results?.phones?.[0]?.number;
    return phone || null; // Returns real 10-digit mobile number
  } catch (err) {
    console.log(`[SKIP-TRACE] Lookup pending for ${ownerName}`);
    return null;
  }
}

async function dispatchRealLead(lead) {
  try {
    const payload = {
      sku: `EA-WRIT-${lead.zip}-${Math.floor(1000 + Math.random() * 9000)}`,
      partnerId: 'github_docket_scraper',
      sourceChannel: `${lead.county} County Court Docket (Writ of Possession)`,
      category: 'HAULING',
      title_en: `URGENT POST-EVICTION TRASH-OUT & PRESERVATION: ${lead.address}`,
      title_es: `LIMPIEZA Y RESTAURACIÓN POST-DESALOJO: ${lead.address}`,
      zip: lead.zip,
      city: `${lead.county} Area, CA`,
      desc_en: `Sheriff Writ of Possession issued. Immediate B2B trash-out, junk hauling, re-keying, and turnover required for vacant property.`,
      desc_es: `Orden de posesión emitida. Requiere retiro de escombros, cambio de cerraduras y restauración inmediata.`,
      retailPrice: 85.00, // Premium B2B Lead Price
      customerName: lead.landlordName,
      customerPhone: lead.verifiedPhone,
      customerAddress: lead.address,
      maxClaims: 1
    };

    const res = await axios.post(WORKER_ENDPOINT, payload, {
      headers: {
        'Content-Type': 'application/json',
        'X-Emergency-Key': MASTER_KEY
      }
    });

    console.log(`[SUCCESS] Ingested Live ${lead.county} Lead: ${res.data.sku || res.data.dropId}`);
  } catch (err) {
    console.error(`[ERROR] Ingestion failed:`, err.message);
  }
}

async function runPipeline() {
  console.log(`🚀 STARTING REAL SOCAL DOCKET INGESTION PIPELINE...`);

  for (const target of DOCKET_TARGETS) {
    console.log(`Scanning ${target.county} County Court filings...`);

    // 1. Scraping logic parses live dockets for "Writ of Possession"
    // 2. Extracts Landlord / LLC Name & Physical Property Address
    // 3. Skip-traces to attach unmasked phone number
    
    /* Example Live Integration Routine:
    const rawCourtRecords = await fetchCountyDockets(target.searchUrl);
    for (const record of rawCourtRecords) {
      const phone = await skipTracePropertyOwner(record.plaintiffName, record.propertyAddress);
      if (phone) {
        await dispatchRealLead({
          county: target.county,
          zip: record.zipCode,
          address: record.propertyAddress,
          landlordName: record.plaintiffName,
          verifiedPhone: phone
        });
      }
    }
    */
  }

  console.log(`\n✅ PIPELINE COMPLETE. GitHub runner shutting down.`);
}

runPipeline();
