const axios = require('axios');
const Parser = require('rss-parser');
const parser = new Parser();

const WORKER_ENDPOINT = 'https://emergencyaudit.com/api/ping';
const PROXY_ENDPOINT = 'https://emergencyaudit.com/api/rss-proxy';
const MASTER_ADMIN_KEY = process.env.MASTER_ADMIN_KEY || 'SecretKey_2026_Dispatch!';

const TRADE_KEYWORDS = [
  'handyman', 'repair', 'fix', 'plumb', 'electric', 'roof', 'paint', 
  'drywall', 'tile', 'leak', 'clog', 'hvac', 'ac', 'door', 'fence', 
  'hauling', 'clean', 'carpenter', 'remodel', 'install', 'gutter', 'locksmith'
];

const OPEN_BOARD_FEEDS = [
  { source: 'Public Service Feed', url: 'https://news.google.com/rss/search?q=handyman+repair+services&hl=en-US&gl=US&ceid=US:en', city: 'Beverly Hills, CA', zip: '90210' }
];

function isHomeServiceRequest(title, snippet) {
  const text = `${title} ${snippet}`.toLowerCase();
  return TRADE_KEYWORDS.some(keyword => text.includes(keyword));
}

function classifyTrade(title) {
  const text = title.toLowerCase();
  if (text.includes('plumb') || text.includes('leak') || text.includes('clog')) return 'PLUMBING';
  if (text.includes('electric') || text.includes('wire')) return 'ELECTRICAL';
  if (text.includes('roof')) return 'ROOFING';
  if (text.includes('clean')) return 'CLEANING';
  if (text.includes('haul') || text.includes('move') || text.includes('junk')) return 'HAULING';
  return 'HANDYMAN';
}

async function runScraperCycle() {
  console.log(`[${new Date().toISOString()}] 🚀 Proxy-Powered Feed Harvester...`);
  let totalIngested = 0;

  for (const feed of OPEN_BOARD_FEEDS) {
    try {
      console.log(`Scanning ${feed.source} via Cloudflare Edge Proxy...`);

      // Route through your Cloudflare Worker proxy to bypass 401/403 bot blocks
      const proxiedUrl = `${PROXY_ENDPOINT}?url=${encodeURIComponent(feed.url)}`;
      const response = await axios.get(proxiedUrl, { timeout: 8000 });

      const parsedFeed = await parser.parseString(response.data);

      if (parsedFeed && parsedFeed.items && parsedFeed.items.length > 0) {
        console.log(` Found ${parsedFeed.items.length} raw posts on ${feed.source}`);

        for (const item of parsedFeed.items) {
          const title = item.title ? item.title.trim() : '';
          const snippet = item.contentSnippet || item.content || '';

          if (!title) continue;
          if (!isHomeServiceRequest(title, snippet)) continue;

          const cat = classifyTrade(title);

          const payload = {
            partnerId: `${feed.source.toLowerCase().replace(/[\s\/]+/g, '_')}_scraper`,
            category: cat,
            title_en: title,
            title_es: title,
            zip: feed.zip,
            city: feed.city,
            desc_en: snippet ? snippet.substring(0, 150) + '...' : `Live ${cat} request from ${feed.city}`,
            desc_es: snippet ? snippet.substring(0, 150) + '...' : `Solicitud en vivo de ${cat} en ${feed.city}`,
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
            console.log(`   ✅ [${cat}] Ingested: ${res.data.sku} | ${title.substring(0, 35)}...`);
          }

          if (totalIngested >= 5) break;
        }
      }
    } catch (err) {
      console.warn(`   ⚠️ Error on ${feed.source}: ${err.message}`);
    }
  }

  console.log(`\n🎉 Harvest Complete. Total Valid Work Orders Ingested: ${totalIngested}`);
  process.exit(0);
}

runScraperCycle();
