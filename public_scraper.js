const axios = require('axios');

const WORKER_ENDPOINT = 'https://emergencyaudit.com/api/ping';
const MASTER_ADMIN_KEY = process.env.MASTER_ADMIN_KEY || 'SecretKey_2026_Dispatch!';

// Comprehensive Multi-Platform Public Feed Array
const MULTI_BOARD_FEEDS = [
  // --- CRAIGSLIST FEEDS ---
  { source: 'Craigslist', url: 'https://losangeles.craigslist.org/search/sfv/sks?format=rss', city: 'Van Nuys / SFV, CA', zip: '91401', cat: 'HANDYMAN' },
  { source: 'Craigslist', url: 'https://losangeles.craigslist.org/search/wst/sks?format=rss', city: 'Beverly Hills, CA', zip: '90210', cat: 'HANDYMAN' },
  { source: 'Craigslist', url: 'https://losangeles.craigslist.org/search/lgs?format=rss', city: 'Hollywood / LA, CA', zip: '90028', cat: 'HAULING' },
  { source: 'Craigslist', url: 'https://orangecounty.craigslist.org/search/sks?format=rss', city: 'Orange County, CA', zip: '92660', cat: 'HANDYMAN' },

  // --- LOCANTO LOCAL SERVICES ---
  { source: 'Locanto', url: 'https://www.locanto.com/Services/S/', city: 'Los Angeles, CA', zip: '90012', cat: 'HANDYMAN' },

  // --- CLASSIFIEDADS.COM SERVICE FEEDS ---
  { source: 'ClassifiedAds', url: 'https://www.classifiedads.com/services-cat.xml', city: 'Greater LA Area, CA', zip: '90001', cat: 'HANDYMAN' },

  // --- GEEBO LOCAL TRADE REQUESTS ---
  { source: 'Geebo', url: 'https://geebo.com/rss/services', city: 'Southern California', zip: '90210', cat: 'HANDYMAN' }
];

async function runMasterScraperCycle() {
  console.log(`[${new Date().toISOString()}] 🌐 Harvesting Multi-Board Network Feeds...`);
  let totalIngested = 0;

  for (const item of MULTI_BOARD_FEEDS) {
    try {
      console.log(`[${item.source}] Harvesting feed for ${item.city}...`);
      const apiUrl = `https://api.rss2json.com/v1/api.json?rss_url=${encodeURIComponent(item.url)}`;
      const response = await axios.get(apiUrl, { timeout: 6000 });

      if (response.data && response.data.status === 'ok' && response.data.items) {
        const posts = response.data.items;
        console.log(` Found ${posts.length} listings on ${item.source} (${item.city})`);

        for (let i = 0; i < Math.min(posts.length, 3); i++) {
          const post = posts[i];
          const title = post.title ? post.title.trim() : '';
          const link = post.link || '';
          const snippet = post.description ? post.description.replace(/<[^>]*>?/gm, '').trim() : '';

          if (!title) continue;

          const payload = {
            partnerId: `${item.source.toLowerCase()}_scraper`,
            category: item.cat,
            title_en: `[${item.source}] ${title}`,
            title_es: `[${item.source}] ${title}`,
            zip: item.zip,
            city: item.city,
            desc_en: snippet ? snippet.substring(0, 160) + '...' : `Live lead from ${item.source} (${item.city})`,
            desc_es: snippet ? snippet.substring(0, 160) + '...' : `Solicitud en vivo de ${item.source} (${item.city})`,
            wholesaleCost: 0.00,
            retailPrice: 25.00,
            customerName: `Verified ${item.source} Poster`,
            customerPhone: 'Unlocked Upon Purchase',
            customerAddress: link
          };

          const res = await axios.post(WORKER_ENDPOINT, payload, {
            headers: {
              'Content-Type': 'application/json',
              'X-Emergency-Key': MASTER_ADMIN_KEY
            },
            timeout: 5000
          });

          if (res.data && res.data.success) {
            totalIngested++;
            console.log(`   ✅ Ingested: ${res.data.sku} | ${title.substring(0, 30)}...`);
          }
        }
      } else {
        console.log(`   ⚠️ No active items returned for ${item.source} (${item.city})`);
      }
    } catch (err) {
      console.error(`   ❌ Failed harvesting ${item.source} - ${item.city}:`, err.message);
    }
  }

  console.log(`\n🎉 Multi-Board Sweep Complete! Total Leads Streamed: ${totalIngested}`);
}

runMasterScraperCycle();
