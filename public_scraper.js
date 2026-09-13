const axios = require('axios');
const cheerio = require('cheerio');

const WORKER_ENDPOINT = 'https://emergencyaudit.com/api/ping';
const MASTER_ADMIN_KEY = process.env.MASTER_ADMIN_KEY || 'SecretKey_2026_Dispatch!';

const CRAIGSLIST_FEEDS = [
  { url: 'https://losangeles.craigslist.org/search/sfv/sks?format=rss', city: 'Van Nuys / SFV, CA', zip: '91401', cat: 'HANDYMAN' },
  { url: 'https://losangeles.craigslist.org/search/wst/sks?format=rss', city: 'Beverly Hills / Westside, CA', zip: '90210', cat: 'HANDYMAN' },
  { url: 'https://losangeles.craigslist.org/search/lac/sks?format=rss', city: 'Downtown LA, CA', zip: '90001', cat: 'HANDYMAN' },
  { url: 'https://losangeles.craigslist.org/search/lgs?format=rss', city: 'Hollywood / LA, CA', zip: '90028', cat: 'HAULING' },
  { url: 'https://orangecounty.craigslist.org/search/sks?format=rss', city: 'Newport Beach / OC, CA', zip: '92660', cat: 'HANDYMAN' },
  { url: 'https://inlandempire.craigslist.org/search/sks?format=rss', city: 'Riverside / IE, CA', zip: '92501', cat: 'HANDYMAN' },
  { url: 'https://sandiego.craigslist.org/search/sks?format=rss', city: 'San Diego, CA', zip: '92101', cat: 'HANDYMAN' },
  { url: 'https://sfbay.craigslist.org/search/sfc/sks?format=rss', city: 'San Francisco, CA', zip: '94102', cat: 'HANDYMAN' }
];

async function runScraperCycle() {
  console.log(`[${new Date().toISOString()}] 🚀 Harvesting Live Craigslist Feeds...`);

  for (const source of CRAIGSLIST_FEEDS) {
    try {
      const response = await axios.get(source.url, {
        headers: { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' }
      });

      const $ = cheerio.load(response.data, { xmlMode: true });
      const items = $('item');

      for (let i = 0; i < Math.min(items.length, 3); i++) {
        const element = items[i];
        const rawTitle = $(element).find('title').text().replace(/<!\[CDATA\[|\]\]>/g, '').trim();
        const rawLink = $(element).find('link').text().trim();
        const description = $(element).find('description').text().replace(/<[^>]*>?/gm, '').trim();

        if (!rawTitle) continue;

        const payload = {
          partnerId: 'public_board_scraper',
          category: source.cat,
          title_en: rawTitle,
          title_es: rawTitle,
          zip: source.zip,
          city: source.city,
          desc_en: description ? description.substring(0, 150) + '...' : `Live request from ${source.city}`,
          desc_es: description ? description.substring(0, 150) + '...' : `Solicitud en vivo de ${source.city}`,
          wholesaleCost: 0.00,
          retailPrice: 25.00,
          customerName: 'Verified Board Poster',
          customerPhone: 'Unlocked Upon Purchase',
          customerAddress: rawLink
        };

        const res = await axios.post(WORKER_ENDPOINT, payload, {
          headers: {
            'Content-Type': 'application/json',
            'X-Emergency-Key': MASTER_ADMIN_KEY
          }
        });

        if (res.data.success) {
          console.log(`✅ Ingested: ${res.data.sku} | ${rawTitle.substring(0, 40)}...`);
        }
      }
    } catch (err) {
      console.error(`❌ Error scraping ${source.city}:`, err.response?.data || err.message);
    }
  }
}

runScraperCycle();
