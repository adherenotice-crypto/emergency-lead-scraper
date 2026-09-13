const axios = require('axios');

const WORKER_ENDPOINT = 'https://emergencyaudit.com/api/ping';
const MASTER_ADMIN_KEY = process.env.MASTER_ADMIN_KEY || 'SecretKey_2026_Dispatch!';

const OPEN_BOARD_FEEDS = [
  { source: 'Global Feed Directory', url: 'https://www.feedforall.com/sample.rss', city: 'Los Angeles, CA', zip: '90001' }
];

async function runScraperCycle() {
  console.log(`[${new Date().toISOString()}] 🚀 Bulletproof Regex Harvester Active...`);
  let totalIngested = 0;

  for (const feed of OPEN_BOARD_FEEDS) {
    try {
      console.log(`Scanning ${feed.source}...`);

      const response = await axios.get(feed.url, {
        headers: {
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
        },
        timeout: 6000
      });

      const xmlText = response.data;
      const items = xmlText.match(/<item[\s\S]*?<\/item>/gi) || xmlText.match(/<entry[\s\S]*?<\/entry>/gi) || [];
      console.log(` Found ${items.length} raw items on ${feed.source}`);

      for (const itemXml of items.slice(0, 3)) {
        const titleMatch = itemXml.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
        const linkMatch = itemXml.match(/<link[^>]*>([\s\S]*?)<\/link>/i) || itemXml.match(/<link\s+href="([^"]*)"/i);
        const descMatch = itemXml.match(/<description[^>]*>([\s\S]*?)<\/description>/i) || itemXml.match(/<summary[^>]*>([\s\S]*?)<\/summary>/i);

        if (!titleMatch || !titleMatch[1]) continue;

        let title = titleMatch[1].replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, '$1').replace(/<[^>]*>?/gm, '').trim();
        let link = linkMatch ? (linkMatch[1] || linkMatch[0]).replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, '$1').replace(/<[^>]*>?/gm, '').trim() : feed.url;
        let snippet = descMatch ? descMatch[1].replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, '$1').replace(/<[^>]*>?/gm, '').trim().substring(0, 150) : 'Immediate Service Request';

        const payload = {
          partnerId: `${feed.source.toLowerCase().replace(/[\s\/]+/g, '_')}_scraper`,
          category: 'HANDYMAN',
          title_en: title,
          title_es: title,
          zip: feed.zip,
          city: feed.city,
          desc_en: snippet,
          desc_es: `Solicitud de servicio en ${feed.city}`,
          wholesaleCost: 0.00,
          retailPrice: 25.00,
          customerName: 'Verified Board Poster',
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
          console.log(`   ✅ Ingested: ${res.data.sku} | ${title.substring(0, 35)}...`);
        }
      }
    } catch (err) {
      console.warn(`   ⚠️ Error on ${feed.source}: ${err.message}`);
    }
  }

  console.log(`\n🎉 Harvest Complete. Total Work Orders Ingested: ${totalIngested}`);
  process.exit(0);
}

runScraperCycle();
