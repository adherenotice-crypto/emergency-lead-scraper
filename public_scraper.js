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

async function fetchFeed(targetUrl) {
  // Method 1: Direct fetch with residential browser headers
  try {
    const directRes = await axios.get(targetUrl, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9'
      },
      timeout: 7000
    });
    if (directRes.data && !directRes.data.includes('<title>blocked</title>')) {
      return directRes.data;
    }
  } catch (e) {
    // Fallback to proxy bridge below
  }

  // Method 2: Route through proxy bridge if direct fetch is blocked
  const proxyUrl = `https://api.allorigins.win/raw?url=${encodeURIComponent(targetUrl)}`;
  const proxyRes = await axios.get(proxyUrl, { timeout: 10000 });
  return proxyRes.data;
}

async function runScraperCycle() {
  console.log(`[${new Date().toISOString()}] 🚀 Starting Live Scraper Pipeline...`);
  let totalIngested = 0;

  for (const source of CRAIGSLIST_FEEDS) {
    try {
      console.log(`Fetching feed: ${source.city}...`);
      const xmlData = await fetchFeed(source.url);

      if (!xmlData || xmlData.includes('<title>blocked</title>')) {
        console.error(`⚠️ Feed blocked for ${source.city}`);
        continue;
      }

      const $ = cheerio.load(xmlData, { xmlMode: true });
      const items = $('item');
      console.log(`Found ${items.length} work orders in ${source.city}`);

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
          },
          timeout: 8000
        });

        if (res.data && res.data.success) {
          totalIngested++;
          console.log(`✅ [INGESTED] ${res.data.sku} | ${rawTitle.substring(0, 35)}...`);
        }
      }
    } catch (err) {
      console.error(`❌ Error processing ${source.city}:`, err.response?.data || err.message);
    }
  }

  console.log(`\n🎉 Scraper Finished. Total Leads Loaded: ${totalIngested}`);
}

runScraperCycle();
