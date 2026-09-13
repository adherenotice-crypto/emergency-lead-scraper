const axios = require('axios');

const WORKER_ENDPOINT = 'https://emergencyaudit.com/api/ping';
const MASTER_ADMIN_KEY = process.env.MASTER_ADMIN_KEY || 'SecretKey_2026_Dispatch!';

const TRADE_TEMPLATES = [
  { cat: 'PLUMBING', title: 'EMERGENCY MAIN SEWER LINE CLOG & BACKUP', city: 'Beverly Hills, CA', zip: '90210', desc: 'Raw sewage backing up into guest bathroom. Urgent plumber dispatch required.' },
  { cat: 'ELECTRICAL', title: 'MAIN BREAKER PANEL SPARKING & SMOKING', city: 'Van Nuys / SFV, CA', zip: '91401', desc: 'Smoke and sizzling sound coming from main circuit breaker box.' },
  { cat: 'ROOFING', title: 'SEVERE STORM ROOF LEAK OVER LIVING ROOM', city: 'Newport Beach / OC, CA', zip: '92660', desc: 'Heavy water leaking through ceiling drywall during heavy rainstorm.' },
  { cat: 'HANDYMAN', title: 'FRONT ENTRY SECURITY DOOR JAMMED & BROKEN', city: 'Hollywood / LA, CA', zip: '90028', desc: 'Front door lock broken, home unsecured. Needs immediate repair.' },
  { cat: 'HAULING', title: 'EMERGENCY BASEMENT FLOOD DEBRIS CLEARANCE', city: 'Santa Monica, CA', zip: '90401', desc: 'Sump pump failed, water ruined drywall and flooring. Need immediate hauling.' }
];

async function runScraperCycle() {
  console.log(`[${new Date().toISOString()}] 🚀 Autonomous Work Order Generator Active...`);
  let totalIngested = 0;

  const shuffled = TRADE_TEMPLATES.sort(() => 0.5 - Math.random());
  const selectedLeads = shuffled.slice(0, 2);

  for (const template of selectedLeads) {
    try {
      const generatedSku = `EA-AUTO-${template.zip}-${Math.floor(1000 + Math.random() * 9000)}`;
      const dropId = `job_${Date.now()}_${Math.floor(100 + Math.random() * 900)}`;

      const payload = {
        sku: generatedSku,
        dropId: dropId,
        partnerId: 'autonomous_dispatch_engine',
        category: template.cat,
        title_en: template.title,
        title_es: template.title,
        zip: template.zip,
        city: template.city,
        desc_en: template.desc,
        desc_es: template.desc,
        wholesaleCost: 0.00,
        retailPrice: 25.00,
        customerName: 'Verified Homeowner',
        customerPhone: 'Unlocked Upon Purchase',
        customerAddress: `Service Reference Address (${template.city})`,
        status: 'AVAILABLE'
      };

      console.log(`Dispatching ${template.cat} lead for ${template.city}...`);

      const res = await axios.post(WORKER_ENDPOINT, payload, {
        headers: {
          'Content-Type': 'application/json',
          'X-Emergency-Key': MASTER_ADMIN_KEY
        },
        timeout: 5000
      });

      if (res.data && res.data.success) {
        totalIngested++;
        console.log(`   ✅ Successfully Ingested: ${res.data.sku} | ${template.title}`);
      }
    } catch (err) {
      console.error(`   ❌ Ingestion error:`, err.response?.data || err.message);
    }
  }

  console.log(`\n🎉 Cycle Complete. Total Live Work Orders Ingested: ${totalIngested}`);
  process.exit(0);
}

runScraperCycle();
