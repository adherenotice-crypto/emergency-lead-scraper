const axios = require('axios');
const Parser = require('rss-parser');
const parser = new Parser();

const WORKER_ENDPOINT = 'https://emergencyaudit.com/api/ping';
const MASTER_ADMIN_KEY = process.env.MASTER_ADMIN_KEY || 'SecretKey_2026_Dispatch!';

// Strict trade keywords to ensure ONLY relevant contractor work orders pass through
const TRADE_KEYWORDS = [
  'handyman', 'repair', 'fix', 'plumb', 'electric', 'roof', 'paint', 
  'drywall', 'tile', 'leak', 'clog', 'hvac', 'ac', 'door', 'fence', 
  'hauling', 'clean', 'carpenter', 'remodel', 'install', 'gutter', 'locksmith'
];

// Open, non-blocking RSS feeds for local service boards
const OPEN_BOARD_FEEDS = [
  { source: 'ClassifiedAds LA', url: 'https://www.classifiedads.com/services-cat.xml', city: 'Los Angeles, CA', zip: '90001', defaultCat: 'HANDYMAN' },
  { source: 'Geebo Trade Gigs', url: 'https://geebo.com/rss/jobs/services', city: 'Southern California', zip: '90210', defaultCat: 'HANDYMAN' },
  { source: 'USFreeAds Services', url: 'https://www.usfreeads.com/rss/services.xml', city: 'California Region', zip: '91401', defaultCat: 'HAULING' }
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
  console.log(`[${new Date().toISOString()}] 🚀 Filtering & Ingesting Strict Trade Leads...`);
  let totalIngested = 0;

  for (const feed of OPEN_BOARD_FEEDS) {
    try {
      console.log(`Scanning ${feed.source}...`);

      const response = await axios.get(feed.url, {
        headers: { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36' },
        timeout: 8000
      });

      const parsedFeed = await parser.parseString(response.data);

      if (parsedFeed && parsedFeed.items) {
        for (const item of parsedFeed.items) {
          const title = item.title ? item.title.trim() : '';
          const snippet = item.contentSnippet || item.content || '';

          if (!title) continue;

          // STRICT FILTER: Skip non-trade / non-homeowner requests
          if (!isHomeServiceRequest(title, snippet)) {
            continue;
          }

          const cat = classifyTrade(title);

          const payload = {
            partnerId: `${feed.source.toLowerCase().replace(/\s+/g, '_')}_scraper`,
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

          if (totalIngested >= 10) break; // Keep output clean per run
        }
      }
    } catch (err) {
      console.error(`   ❌ Error on ${feed.source}:`, err.message);
    }
  }

  console.log(`\n🎉 Targeted Trade Harvest Complete. Total Valid Work Orders: ${totalIngested}`);
  process.exit(0);
}

runScraperCycle();
