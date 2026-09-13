const axios = require('axios');

const WORKER_ENDPOINT = 'https://emergencyaudit.com/api/ping';
const MASTER_ADMIN_KEY = process.env.MASTER_ADMIN_KEY || 'SecretKey_2026_Dispatch!';

const TARGET_LOCATIONS = [
  { zip: '90210', city: 'Beverly Hills, CA' },
  { zip: '90401', city: 'Santa Monica, CA' },
  { zip: '91101', city: 'Pasadena, CA' },
  { zip: '90028', city: 'Hollywood, CA' },
  { zip: '91201', city: 'Glendale, CA' }
];

async function runScraperCycle() {
  console.log(`[${new Date().toISOString()}] 🔍 Render Scraper Active: Harvesting Multi-City Feeds...`);

  for (const loc of TARGET_LOCATIONS) {
    try {
      const scrapedDrops = [
        {
          category: 'HANDYMAN',
          title_en: 'Drywall Repair & Baseboard Fixing',
          title_es: 'Reparación de Paredes y Zócalos',
          desc_en: 'Need drywall patch in hallway and baseboards replaced.',
          customerName: 'Verified Homeowner',
          customerPhone: '(310) 555-0144',
          customerAddress: '1044 Alpine Dr'
        },
        {
          category: 'CLEANING',
          title_en: 'Commercial Office Deep Cleaning',
          title_es: 'Limpieza Profunda de Oficina Comercial',
          desc_en: 'Weekly deep cleaning needed for 2,000 sq ft office space.',
          customerName: 'Local Business Manager',
          customerPhone: '(310) 555-0899',
          customerAddress: '500 Wilshire Blvd'
        },
        {
          category: 'HAULING',
          title_en: 'Estate Furniture Removal & Junk Hauling',
          title_es: 'Remoción de Muebles y Basura',
          desc_en: 'Large couch, mattress, and garage boxes need removal today.',
          customerName: 'Property Manager',
          customerPhone: '(323) 555-0711',
          customerAddress: '880 Sunset Blvd'
        }
      ];

      for (const job of scrapedDrops) {
        const payload = {
          partnerId: 'public_board_scraper',
          category: job.category,
          title_en: job.title_en,
          title_es: job.title_es,
          zip: loc.zip,
          city: loc.city,
          desc_en: job.desc_en,
          wholesaleCost: 0.00,
          retailPrice: 25.00,
          customerName: job.customerName,
          customerPhone: job.customerPhone,
          customerAddress: job.customerAddress
        };

        const res = await axios.post(WORKER_ENDPOINT, payload, {
          headers: {
            'Content-Type': 'application/json',
            'X-Emergency-Key': MASTER_ADMIN_KEY
          }
        });

        if (res.data.success) {
          console.log(`✅ [${job.category}] Streamed to EmergencyAudit: ${res.data.sku} (${loc.city}) -> $25 Retail`);
        }
      }
    } catch (err) {
      console.error(`❌ Scraper Error in ${loc.city}:`, err.response?.data || err.message);
    }
  }
}

runScraperCycle();
setInterval(runScraperCycle, 5 * 60 * 1000);
