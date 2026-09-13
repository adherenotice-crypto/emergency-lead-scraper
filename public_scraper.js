const axios = require('axios');
const Parser = require('rss-parser');
const parser = new Parser();

const WORKER_ENDPOINT = 'https://emergencyaudit.com/api/ping';
const MASTER_ADMIN_KEY = process.env.MASTER_ADMIN_KEY || 'SecretKey_2026_Dispatch!';

const TARGET_FEEDS = [
  // --- CRAIGSLIST FEEDS ---
  { source: 'Craigslist SFV', url: 'https://losangeles.craigslist.org/search/sfv/sks?format=rss', city: 'Van Nuys / SFV, CA', zip: '91401', cat: 'HANDYMAN' },
  { source: 'Craigslist Westside', url: 'https://losangeles.craigslist.org/search/wst/sks?format=rss', city: 'Beverly Hills, CA', zip: '90210', cat: 'HANDYMAN' },
  { source: 'Craigslist Hollywood', url: 'https://losangeles.craigslist.org/search/lgs?format=rss', city: 'Hollywood, CA', zip: '90028', cat: 'HAULING' },
  { source: 'Craigslist OC', url: 'https://orangecounty.craigslist.org/search/sks?format=rss', city: 'Orange County, CA', zip: '92660', cat: 'HANDYMAN' },

  // --- MULTI-BOARD NETWORK FEEDS ---
  { source: 'Geebo Trade Requests', url: 'https://geebo.com/rss/services', city: 'Southern California', zip: '90210', cat: 'HANDYMAN' },
  { source: 'ClassifiedAds Services', url: 'https://www.classifiedads.com/services-cat.xml', city: 'Greater LA Area, CA', zip: '90001', cat: 'HANDYMAN' }
];

async function runScraperCycle() {
  console.log(`[${new Date().toISOString()}] 🚀 Multi-Site Harvester Active...`);
  let totalIngested = 0;

  for (const feed of TARGET_FEEDS) {
    try {
      console.log(`Fetching ${feed.source}...`);

      // Fetch with desktop browser headers to prevent HTTP 403 blocks
      const response = await axios.get(feed.url, {
        headers: {
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
          'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
          'Accept-Language': 'en-US,en;q=0.9'
        },
        timeout: 8000
      });

      const parsedFeed = await parser.parseString(response.data);

      if (parsedFeed && parsedFeed.items) {
        console.log(` Found ${parsedFeed.items.length} items on ${feed.source}`);

        for (let i = 0; i < Math.min(parsedFeed.items.length, 3); i++) {
          const item = parsedFeed.items[i];
          const title = item.title ? item.title.trim() : '';
          const snippet = item.contentSnippet || item.content || '';

          if (!title) continue;

          const payload = {
            partnerId: `${feed.source.toLowerCase().replace(/\s+/g, '_')}_scraper`,
            category: feed.cat,
            title_en: title,
            title_es: title,
            zip: feed.zip,
            city: feed.city,
            desc_en: snippet ? snippet.substring(0, 150) + '...' : `Live request from ${feed.city}`,
            desc_es: snippet ? snippet.substring(0, 150) + '...' : `Solicitud en vivo de ${feed.city}`,
            wholesaleCost: 0.00,
            retailPrice: 25.00,
            customerName: 'Verified Board Poster',
            customerPhone: 'Unlocked Upon Purchase',
            customerAddress: item.link || feed.url
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
      }
    } catch (err) {
      console.error(`   ❌ Failed harvesting ${feed.source}:`, err.response?.status ? `Status Code ${err.response.status}` : err.message);
    }
  }

  console.log(`\n🎉 Multi-Site Cycle Complete. Total Leads Ingested: ${totalIngested}`);
}

runScraperCycle();
