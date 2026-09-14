const axios = require('axios');

const WORKER_ENDPOINT = process.env.WORKER_API_ENDPOINT || 'https://emergencyaudit.com/api/ping';
const MASTER_KEY = process.env.MASTER_ADMIN_KEY || 'SecretKey_2026_Dispatch!';

// Southern California County Court Targets
const DOCKET_TARGETS = [
  { county: 'Los Angeles', code: 'LASC', defaultZip: '90012', region: 'LA Metro / Valley' },
  { county: 'Orange', code: 'OCSC', defaultZip: '92660', region: 'Orange County' },
  { county: 'San Diego', code: 'SDSC', defaultZip: '92101', region: 'San Diego Metro' },
  { county: 'Riverside', code: 'RIV', defaultZip: '92501', region: 'Inland Empire' }
];

async function dispatchEvictionLead(leadData) {
  try {
    const payload = {
      sku: `EA-WRIT-${leadData.zip}-${Math.floor(1000 + Math.random() * 9000)}`,
      partnerId: 'github_docket_scraper',
      sourceChannel: `${leadData.county} County Court Docket (Writ of Possession)`,
      category: 'HAULING',
      title_en: `EVICTION TRASH-OUT & CLEANOUT: ${leadData.address}`,
      title_es: `LIMPIEZA DE DESALOJO: ${leadData.address}`,
      zip: leadData.zip,
      city: `${leadData.county} Area, CA`,
      desc_en: `Writ of Possession issued. Immediate post-eviction unit turnover, junk hauling, and re-keying required.`,
      desc_es: `Orden de posesión emitida. Se requiere retiro de basura y cambio de cerraduras de inmediato.`,
      retailPrice: 50.00,
      customerName: leadData.landlordOrAttorney,
      customerPhone: leadData.contactPhone,
      customerAddress: leadData.address,
      maxClaims: 1
    };

    const res = await axios.post(WORKER_ENDPOINT, payload, {
      headers: {
        'Content-Type': 'application/json',
        'X-Emergency-Key': MASTER_KEY
      }
    });

    console.log(`[SUCCESS] Ingested ${leadData.county} Lead: ${res.data.sku || res.data.dropId}`);
  } catch (err) {
    console.error(`[ERROR] Dispatch failed for ${leadData.county}:`, err.response?.data || err.message);
  }
}

async function runPipeline() {
  console.log(`🚀 STARTING SOCAL EVICTION DOCKET INGESTION CYCLE...`);

  for (const target of DOCKET_TARGETS) {
    console.log(`Scanning ${target.county} County Court Portals for "Writ of Possession Issued"...`);
    
    const mockParsedRecord = {
      county: target.county,
      zip: target.defaultZip,
      address: `Service Property Address (${target.region})`,
      landlordOrAttorney: `Plaintiff Attorney / Property Mgr (${target.code}-UD)`,
      contactPhone: `+1 (310) 555-0199`
    };

    await dispatchEvictionLead(mockParsedRecord);
  }

  console.log(`\n✅ PIPELINE CYCLE COMPLETE. Shutting down GitHub runner.`);
}

runPipeline();
