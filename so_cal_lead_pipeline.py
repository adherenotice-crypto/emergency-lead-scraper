/**
 * ============================================================================
 * EMERGENCYAUDIT.com! | Complete Master Edge Routing & Command Engine
 * ============================================================================
 */

function formatPhoneNumber(phone) {
  if (!phone) return '1-800-555-0199';
  const clean = phone.replace(/\D/g, '');
  if (clean.length === 11 && clean.startsWith('1')) {
    return `1-${clean.slice(1,4)}-${clean.slice(4,7)}-${clean.slice(7)}`;
  }
  if (clean.length === 10) {
    return `1-${clean.slice(0,3)}-${clean.slice(3,6)}-${clean.slice(6)}`;
  }
  return phone;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    const corsHeaders = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, X-Emergency-Key',
      'X-Engine': 'EMERGENCYAUDIT.com! Complete Edge Engine'
    };

    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: corsHeaders });
    }

    try {
      const kv = env.LEADS_KV || env.emergency_audit_leads || env.KV || null;
      const isPaused = kv ? (await kv.get('system_paused_state')) === 'true' : false;

      // ROUTE 1: PUBLIC CORPORATE LANDING PAGE (DOOR 1)
      if (url.pathname === '/' || url.pathname === '/index.html' || url.pathname === '/about') {
        return new Response(getCorporateHTML(isPaused), {
          status: 200,
          headers: { 'Content-Type': 'text/html;charset=UTF-8', ...corsHeaders }
        });
      }

      // ROUTE 2: CLEAN SHORT-URL DYNAMIC CASE ROUTE (/c/AUD-XXXXXX OR /case)
      const pathParts = url.pathname.split('/').filter(Boolean);
      if (pathParts[0] === 'c' || pathParts[0] === 'case' || url.pathname === '/audit') {
        const caseId = pathParts[1] || url.searchParams.get('id') || 'AUD-8842';
        
        let address = url.searchParams.get('address');
        let ownerName = url.searchParams.get('owner') || url.searchParams.get('owner_name');
        let ownerPhone = url.searchParams.get('phone');
        let supportPhone = url.searchParams.get('support_phone');
        let apn = url.searchParams.get('apn');
        let violation = url.searchParams.get('violation');
        let rawCode = url.searchParams.get('raw_code');
        let yearBuilt = url.searchParams.get('year_built');
        let sqft = url.searchParams.get('sqft');
        let zoning = url.searchParams.get('zoning');
        let propertyUse = url.searchParams.get('property_use');
        let category = 'COMMERCIAL';

        // SERVER-SIDE KV LOOKUP (Pulls complete dossier payload)
        if (kv && caseId) {
          try {
            const rawData = await kv.get(`job_${caseId}`);
            if (rawData) {
              const parsed = JSON.parse(rawData);
              address = parsed.address || address;
              ownerName = parsed.owner_name || ownerName;
              apn = parsed.apn || apn;
              violation = parsed.violation || parsed.desc_en || violation;
              rawCode = parsed.raw_code || rawCode;
              category = parsed.category || category;
              ownerPhone = parsed.phone || ownerPhone;
              supportPhone = parsed.support_phone || supportPhone;
              yearBuilt = parsed.year_built || yearBuilt;
              sqft = parsed.sqft || sqft;
              zoning = parsed.zoning || zoning;
              propertyUse = parsed.property_use || propertyUse;
            } else {
              const list = await kv.list({ prefix: 'job_' });
              for (const k of list.keys) {
                if (k.name.includes(caseId)) {
                  const itemRaw = await kv.get(k.name);
                  if (itemRaw) {
                    const p = JSON.parse(itemRaw);
                    address = p.address || address;
                    ownerName = p.owner_name || ownerName;
                    apn = p.apn || apn;
                    violation = p.violation || p.desc_en || violation;
                    rawCode = p.raw_code || rawCode;
                    category = p.category || category;
                    ownerPhone = p.phone || ownerPhone;
                    supportPhone = p.support_phone || supportPhone;
                    yearBuilt = p.year_built || yearBuilt;
                    sqft = p.sqft || sqft;
                    zoning = p.zoning || zoning;
                    propertyUse = p.property_use || propertyUse;
                    break;
                  }
                }
              }
            }
          } catch (e) {
            console.error('KV Lookup Error:', e);
          }
        }

        apn = apn || 'N/A';
        ownerName = ownerName || 'RECORDED PROPERTY OWNER / ENTITY';
        
        // COMBINE VIOLATION & CODE FOR DETAILED DISPLAY
        if (violation && rawCode && !violation.includes(rawCode)) {
          violation = `${violation} • Code: ${rawCode}`;
        } else if (!violation) {
          violation = 'Municipal Building & Safety Citation Notice (LAMC Order to Comply)';
        }

        // STRICT CALL ROUTING: Route to Buyer Lines or Network 1-800 line
        if (!supportPhone) {
          if (category === 'EMERGENCY' && env.EMERGENCY_BUYER_NUMBER) {
            supportPhone = env.EMERGENCY_BUYER_NUMBER;
          } else if (category === 'TRADE' && env.TRADE_BUYER_NUMBER) {
            supportPhone = env.TRADE_BUYER_NUMBER;
          } else {
            supportPhone = env.NETWORK_1800_NUMBER || '18005550199';
          }
        }

        address = address || 'Commercial / Residential Real Estate Parcel';

        return new Response(getCaseNoticeHTML({
          caseId,
          address,
          ownerName,
          phoneRaw: supportPhone,
          phoneFormatted: formatPhoneNumber(supportPhone),
          apn,
          violation,
          yearBuilt: yearBuilt || 'N/A',
          sqft: sqft || 'N/A',
          zoning: zoning || 'N/A',
          propertyUse: propertyUse || 'REAL ESTATE PARCEL'
        }), {
          status: 200,
          headers: { 'Content-Type': 'text/html;charset=UTF-8', ...corsHeaders }
        });
      }

      // ROUTE 3: PRIVATE BACK OFFICE DASHBOARD
      if (url.pathname === '/admin/dashboard') {
        let leads = [];
        if (kv) {
          try {
            const list = await kv.list({ prefix: 'job_' });
            if (list.keys.length > 0) {
              const rawItems = await Promise.all(list.keys.map(k => kv.get(k.name)));
              leads = rawItems.filter(Boolean).map(item => JSON.parse(item));
            }
          } catch (e) {
            console.error('KV Read Error:', e);
          }
        }
        return new Response(getDashboardHTML(leads, isPaused), {
          status: 200,
          headers: { 'Content-Type': 'text/html;charset=UTF-8', ...corsHeaders }
        });
      }

      // ROUTE 4: API STATUS
      if (url.pathname === '/api/status') {
        return new Response(JSON.stringify({ status: isPaused ? 'PAUSED' : 'ACTIVE', paused: isPaused }), {
          status: 200,
          headers: { 'Content-Type': 'application/json', ...corsHeaders }
        });
      }

      // ROUTE 4B: RAW KV LEADS JSON FOR PIPELINE CACHE LOOKUP
      if (url.pathname === '/api/leads') {
        let cache = {};
        if (kv) {
          try {
            const list = await kv.list({ prefix: 'job_' });
            for (const k of list.keys) {
              const raw = await kv.get(k.name);
              if (raw) {
                const item = JSON.parse(raw);
                if (item.address) cache[item.address] = item;
                if (item.citation_id) cache[item.citation_id] = item;
              }
            }
          } catch (e) {
            console.error('KV Cache Fetch Error:', e);
          }
        }
        return new Response(JSON.stringify(cache), {
          status: 200,
          headers: { 'Content-Type': 'application/json', ...corsHeaders }
        });
      }

      // ROUTE 5: SCRAPED LEAD DISPATCH INGRESS (WITH IMMUTABLE OVERWRITE PROTECTION)
      if (url.pathname === '/api/dispatch' && request.method === 'POST') {
        const payload = await request.json();
        const dropId = payload.dropId || `job_${payload.citation_id || Date.now()}`;
        payload.dropId = dropId;
        payload.timestamp = payload.timestamp || new Date().toISOString();

        if (kv) {
          try {
            const existingRaw = await kv.get(dropId);
            if (existingRaw) {
              const existing = JSON.parse(existingRaw);

              // GUARDRAIL 1: Never overwrite verified mobile phone with 'PENDING UNMASK'
              if ((!payload.phone || payload.phone === 'PENDING UNMASK' || payload.phone === 'Unmasked Upon Purchase') && existing.phone && existing.phone !== 'PENDING UNMASK' && existing.phone !== 'Unmasked Upon Purchase') {
                payload.phone = existing.phone;
                payload.customerPhone = existing.phone;
                payload.phone_type = existing.phone_type || 'MOBILE';
              }

              // GUARDRAIL 2: Never overwrite verified email with 'N/A'
              if ((!payload.email || payload.email === 'N/A') && existing.email && existing.email !== 'N/A') {
                payload.email = existing.email;
              }

              // GUARDRAIL 3: Never overwrite unmasked owner name with generic fallback
              if ((!payload.owner_name || payload.owner_name.includes('PROPERTY OWNER')) && existing.owner_name && !existing.owner_name.includes('PROPERTY OWNER')) {
                payload.owner_name = existing.owner_name;
              }

              // GUARDRAIL 4: Preserve existing APN & Specs if incoming payload is 'N/A'
              if ((!payload.apn || payload.apn === 'N/A') && existing.apn && existing.apn !== 'N/A') {
                payload.apn = existing.apn;
                payload.year_built = existing.year_built && existing.year_built !== 'N/A' ? existing.year_built : payload.year_built;
                payload.sqft = existing.sqft && existing.sqft !== 'N/A' ? existing.sqft : payload.sqft;
                payload.zoning = existing.zoning && existing.zoning !== 'N/A' ? existing.zoning : payload.zoning;
                payload.property_use = existing.property_use && existing.property_use !== 'REAL ESTATE PARCEL' ? existing.property_use : payload.property_use;
              }
            }
          } catch (e) {
            console.error('Dispatch Immutable Guardrail Error:', e);
          }

          await kv.put(dropId, JSON.stringify(payload));
        }

        return new Response(JSON.stringify({ success: true, dropId }), {
          status: 200,
          headers: { 'Content-Type': 'application/json', ...corsHeaders }
        });
      }

      // ROUTE 6: BATCH DELETE SELECTED LEADS
      if (url.pathname === '/api/admin/delete-batch' && request.method === 'POST') {
        const { dropIds } = await request.json();
        if (kv && Array.isArray(dropIds)) {
          await Promise.all(dropIds.map(id => kv.delete(id)));
        }
        return new Response(JSON.stringify({ success: true, deletedCount: dropIds ? dropIds.length : 0 }), {
          status: 200,
          headers: { 'Content-Type': 'application/json', ...corsHeaders }
        });
      }

      // ROUTE 7: PURGE DEAD / UNMASKED LEADS ONLY
      if (url.pathname === '/api/admin/purge-unmasked' && request.method === 'POST') {
        let purgedCount = 0;
        if (kv) {
          const list = await kv.list({ prefix: 'job_' });
          for (const k of list.keys) {
            const raw = await kv.get(k.name);
            if (raw) {
              const item = JSON.parse(raw);
              const p = item.phone || item.customerPhone;
              if (!p || p === 'PENDING UNMASK' || p === 'Unmasked Upon Purchase') {
                await kv.delete(k.name);
                purgedCount++;
              }
            }
          }
        }
        return new Response(JSON.stringify({ success: true, purgedCount }), {
          status: 200,
          headers: { 'Content-Type': 'application/json', ...corsHeaders }
        });
      }

      // ROUTE 8: PURGE ALL LEADS
      if (url.pathname === '/api/admin/purge-all' && request.method === 'POST') {
        if (kv) {
          const list = await kv.list({ prefix: 'job_' });
          await Promise.all(list.keys.map(k => kv.delete(k.name)));
        }
        return new Response(JSON.stringify({ success: true }), {
          status: 200,
          headers: { 'Content-Type': 'application/json', ...corsHeaders }
        });
      }

      // ROUTE 9: TOGGLE KILL SWITCH
      if (url.pathname === '/api/admin/toggle-pause' && request.method === 'POST') {
        const newState = !isPaused;
        if (kv) await kv.put('system_paused_state', newState ? 'true' : 'false');
        return new Response(JSON.stringify({ success: true, paused: newState }), {
          status: 200,
          headers: { 'Content-Type': 'application/json', ...corsHeaders }
        });
      }

      // ROUTE 10: BATCH RELEASE STAGED LEADS
      if (url.pathname === '/api/admin/release-staged' && request.method === 'POST') {
        let releasedCount = 0;
        if (kv) {
          const list = await kv.list({ prefix: 'job_' });
          for (const k of list.keys) {
            const raw = await kv.get(k.name);
            if (raw) {
              const item = JSON.parse(raw);
              if (item.status === 'PENDING_REVIEW') {
                item.status = 'READY_FOR_DISPATCH';
                await kv.put(k.name, JSON.stringify(item));
                releasedCount++;
              }
            }
          }
        }
        return new Response(JSON.stringify({ success: true, releasedCount }), {
          status: 200,
          headers: { 'Content-Type': 'application/json', ...corsHeaders }
        });
      }

      // ROUTE 11: RETELL AI WEB-CALL TOKEN GENERATOR
      if (url.pathname === '/api/andrea-web-call' && request.method === 'POST') {
        if (!env.RETELL_API_KEY) {
          return new Response(JSON.stringify({ error: 'RETELL_API_KEY missing' }), { status: 500, headers: corsHeaders });
        }
        const retellRes = await fetch('https://api.retellai.com/v2/create-web-call', {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${env.RETELL_API_KEY}`,
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({ agent_id: env.RETELL_AGENT_ID || 'agent_default' })
        });
        const callData = await retellRes.json();
        return new Response(JSON.stringify(callData), {
          status: 200,
          headers: { 'Content-Type': 'application/json', ...corsHeaders }
        });
      }

      return new Response(getCorporateHTML(isPaused), {
        status: 200,
        headers: { 'Content-Type': 'text/html;charset=UTF-8', ...corsHeaders }
      });

    } catch (err) {
      return new Response(JSON.stringify({ error: err.message }), {
        status: 500,
        headers: { 'Content-Type': 'application/json', ...corsHeaders }
      });
    }
  }
};

// ============================================================================
// FRONTEND HTML TEMPLATES
// ============================================================================

function getCaseNoticeHTML(data) {
  const { caseId, address, ownerName, phoneRaw, phoneFormatted, apn, violation, yearBuilt, sqft, zoning, propertyUse } = data;
  const decodedAddress = decodeURIComponent(address || '');
  const decodedOwner = decodeURIComponent(ownerName || 'RECORDED PROPERTY OWNER / ENTITY');
  const decodedViolation = decodeURIComponent(violation || '');

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0">
  <title>EMERGENCYAUDIT.com! | Record Summary #${caseId}</title>
  <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' rx='20' fill='%231e3a8a'/><text x='50%' y='68%' font-size='55' font-weight='900' fill='white' text-anchor='middle' font-family='sans-serif'>EA</text></svg>">
  <script src="https://cdn.tailwindcss.com"></script>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
  <style>
    body { font-family: 'Inter', -apple-system, sans-serif; }
    .mono { font-family: 'JetBrains Mono', monospace; }
  </style>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen px-3 py-4 sm:p-6 flex justify-center items-center antialiased">
  <div class="w-full max-w-xl bg-slate-900 border border-slate-800 rounded-2xl p-4 sm:p-6 shadow-2xl overflow-hidden">
    
    <!-- HEADER BRANDING -->
    <div class="flex items-center justify-between border-b border-slate-800 pb-4 mb-5 gap-2">
      <div class="leading-none">
        <span class="text-base sm:text-lg font-black tracking-tight text-blue-500">EMERGENCY<span class="text-white">AUDIT</span><span class="text-blue-500">.com!</span></span>
        <span class="text-[9px] sm:text-[10px] font-bold text-slate-400 block tracking-widest uppercase mt-1">Property Notice & Public Audit</span>
      </div>
      <span class="bg-blue-950 text-blue-400 border border-blue-800/80 text-[10px] sm:text-xs font-mono font-bold px-2.5 py-1 rounded-full whitespace-nowrap">
        RECORD INDEXED
      </span>
    </div>

    <!-- MAIN CARD TITLE -->
    <h1 class="text-lg sm:text-xl font-black text-white tracking-tight mb-4 leading-snug">
      Public Record Information Summary
    </h1>

    <!-- DATA GRID -->
    <div class="grid grid-cols-1 sm:grid-cols-2 gap-2.5 mb-4">
      <div class="bg-slate-950 border border-slate-800/80 p-3 rounded-xl">
        <div class="text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-1">Case Reference</div>
        <div class="mono text-sm font-bold text-blue-400 truncate">#${caseId}</div>
      </div>
      <div class="bg-slate-950 border border-slate-800/80 p-3 rounded-xl">
        <div class="text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-1">Parcel APN</div>
        <div class="mono text-sm font-bold text-slate-200 truncate">${apn}</div>
      </div>
      <div class="sm:col-span-2 bg-slate-950 border border-slate-800/80 p-3 rounded-xl">
        <div class="text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-1">Recorded Owner / Entity</div>
        <div class="mono text-xs sm:text-sm font-bold text-amber-400 leading-snug break-words">${decodedOwner}</div>
      </div>
      <div class="sm:col-span-2 bg-slate-950 border border-slate-800/80 p-3 rounded-xl">
        <div class="text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-1">Property Address</div>
        <div class="mono text-xs sm:text-sm font-semibold text-slate-100 leading-snug break-words">${decodedAddress}</div>
      </div>
    </div>

    <!-- STRUCTURAL SPECS GRID -->
    <div class="bg-slate-950 border border-slate-800/80 rounded-xl p-3 mb-4">
      <div class="text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-2 border-b border-slate-800 pb-1.5">
        Structural Overview & Specs
      </div>
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 text-center">
        <div class="bg-slate-900 p-2 rounded-lg border border-slate-800">
          <div class="text-[9px] text-slate-400 uppercase font-bold">Use</div>
          <div class="mono text-[11px] font-bold text-slate-200 truncate">${propertyUse}</div>
        </div>
        <div class="bg-slate-900 p-2 rounded-lg border border-slate-800">
          <div class="text-[9px] text-slate-400 uppercase font-bold">SqFt</div>
          <div class="mono text-[11px] font-bold text-slate-200 truncate">${sqft}</div>
        </div>
        <div class="bg-slate-900 p-2 rounded-lg border border-slate-800">
          <div class="text-[9px] text-slate-400 uppercase font-bold">Built</div>
          <div class="mono text-[11px] font-bold text-slate-200 truncate">${yearBuilt}</div>
        </div>
        <div class="bg-slate-900 p-2 rounded-lg border border-slate-800">
          <div class="text-[9px] text-slate-400 uppercase font-bold">Zoning</div>
          <div class="mono text-[11px] font-bold text-slate-200 truncate">${zoning}</div>
        </div>
      </div>
    </div>

    <!-- INDEXED ISSUE DETAILS -->
    <div class="bg-amber-950/40 border border-amber-800/60 border-l-4 border-l-amber-500 p-3.5 rounded-xl mb-4">
      <div class="text-[10px] font-extrabold uppercase tracking-wider text-amber-400 mb-1">Indexed Issue Details</div>
      <p class="text-xs sm:text-sm text-amber-200 font-medium leading-relaxed break-words">${decodedViolation}</p>
    </div>

    <!-- PUBLIC RECORD ADVISORY GUIDANCE BOX -->
    <div class="bg-slate-950 border border-slate-800 rounded-xl p-4 mb-5">
      <div class="text-[10px] font-extrabold uppercase tracking-wider text-blue-400 mb-2 border-b border-slate-800/80 pb-1.5 flex items-center gap-1.5">
        <span>ℹ️</span> Public Record Advisory Guidance
      </div>
      <div class="space-y-2 text-[11px] sm:text-xs text-slate-300 leading-relaxed">
        <p>
          <strong class="text-white">What This Notice Means:</strong> A municipal order, building code citation, or property compliance flag has been logged on public municipal index records for this parcel.
        </p>
        <p>
          <strong class="text-white">Why You Received This Alert:</strong> EMERGENCYAUDIT.com! monitors municipal feeds to provide early notification before secondary administrative fees, penalties, or compliance deadlines escalate.
        </p>
        <p>
          <strong class="text-white">Recommended Immediate Action:</strong> Contact the support line below to review record status, confirm compliance requirements, or speak with a property resolution specialist.
        </p>
      </div>
    </div>

    <!-- TOUCH-FRIENDLY SUPPORT CTA BOX -->
    <div class="bg-slate-950 border border-slate-800 rounded-xl p-4 text-center shadow-lg">
      <div class="text-xs sm:text-sm font-extrabold text-white mb-1">Need Help Resolving This Record?</div>
      <p class="text-[11px] sm:text-xs text-slate-400 leading-snug mb-3">Speak directly with a property resolution specialist to discuss remediation options.</p>
      
      <a href="tel:${phoneRaw}" class="w-full inline-flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-500 text-white font-extrabold text-xs sm:text-sm py-3.5 px-4 rounded-xl shadow-lg transition active:scale-[0.98]">
        <span>📞</span>
        <span>Call Support Line: ${phoneFormatted}</span>
      </a>
    </div>

    <!-- FOOTER -->
    <div class="text-center text-[10px] text-slate-500 mt-5 pt-3 border-t border-slate-800/80 leading-relaxed">
      <span class="text-blue-500 font-bold">EMERGENCY</span><span class="text-white font-bold">AUDIT</span><span class="text-blue-500 font-bold">.com!</span> • Independent Property Record Monitoring Network
    </div>

  </div>
</body>
</html>`;
}

function getCorporateHTML(isPaused) {
  return `<!DOCTYPE html>
<html lang="en" class="scroll-smooth">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>EMERGENCYAUDIT.com! | Property Notice & Resolution Support Network</title>
    
    <meta name="description" content="EMERGENCYAUDIT.com! connects property owners facing municipal code citations, building orders, or tax liens with certified resolution specialists.">
    <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' rx='20' fill='%231e3a8a'/><text x='50%' y='68%' font-size='55' font-weight='900' fill='white' text-anchor='middle' font-family='sans-serif'>EA</text></svg>">

    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/retell-client-js-sdk@latest/dist/retell-client-js-sdk.umd.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet">
    <style> body { font-family: 'Inter', sans-serif; } </style>
</head>
<body class="bg-slate-950 text-slate-100 antialiased relative">
    <div class="bg-blue-950 text-blue-200 text-[11px] sm:text-xs py-2 px-3 sm:px-6 border-b border-blue-900">
        <div class="max-w-7xl mx-auto flex justify-between items-center gap-2">
            <span class="flex items-center gap-2 truncate">
                <span class="w-2 h-2 rounded-full ${isPaused ? 'bg-amber-400' : 'bg-emerald-400'} animate-pulse shrink-0"></span>
                <strong class="truncate">STATUS: ${isPaused ? 'MAINTENANCE' : 'Property Record Feeds Active Across 50 States'}</strong>
            </span>
            <span class="hidden md:inline text-slate-400 shrink-0">Property Owner Resolution Network</span>
        </div>
    </div>

    <header class="bg-slate-900/90 backdrop-blur-md border-b border-slate-800 sticky top-0 z-50">
        <div class="max-w-7xl mx-auto px-3 sm:px-6 h-16 sm:h-20 flex items-center justify-between gap-2">
            <div class="flex items-center space-x-2 sm:space-x-2.5 shrink-0">
                <div class="bg-blue-600 text-white font-black px-2 py-1 sm:px-2.5 sm:py-1.5 rounded-lg text-base sm:text-lg tracking-wider">EA</div>
                <div>
                    <span class="text-sm sm:text-xl font-extrabold tracking-tight text-blue-500 block leading-none">EMERGENCY<span class="text-white">AUDIT</span><span class="text-blue-500">.com!</span></span>
                    <span class="text-[8px] sm:text-[10px] font-bold text-slate-400 tracking-widest uppercase mt-0.5 block">Property Resolution Network</span>
                </div>
            </div>
            <a href="#how-it-works" class="bg-blue-600 hover:bg-blue-500 text-white text-[11px] sm:text-sm font-bold px-3 sm:px-5 py-2 sm:py-2.5 rounded-lg shadow-sm transition whitespace-nowrap shrink-0">Get Assistance</a>
        </div>
    </header>

    <section class="relative py-12 sm:py-28 overflow-hidden">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 relative z-10 grid grid-cols-1 lg:grid-cols-12 gap-8 lg:gap-12 items-center">
            <div class="lg:col-span-7">
                <div class="inline-flex items-center space-x-2 bg-blue-950 border border-blue-800/80 px-3 py-1 rounded-full text-[11px] sm:text-xs font-semibold text-blue-300 mb-4">
                    <span>Property & Citation Resolution Assistance</span>
                </div>
                <h1 class="text-3xl sm:text-5xl lg:text-6xl font-black tracking-tight leading-tight mb-4">
                    National Property Citation & <span class="text-blue-400">Resolution Network</span>
                </h1>
                <p class="text-base sm:text-xl text-slate-300 font-medium leading-relaxed mb-6 border-l-4 border-blue-500 pl-4 py-1">
                    Connecting property owners facing municipal citations or tax liens directly with certified resolution services.
                </p>
                <div class="flex flex-col sm:flex-row gap-3">
                    <button onclick="startAndreaWebCall()" class="w-full sm:w-auto bg-emerald-600 hover:bg-emerald-500 text-white font-bold px-6 py-3.5 rounded-xl shadow-lg transition text-center flex items-center justify-center space-x-2 text-sm">
                        <span>🎙️ Talk to Andrea (AI Assistant)</span>
                    </button>
                    <a href="#how-it-works" class="w-full sm:w-auto bg-blue-600 hover:bg-blue-500 text-white font-bold px-6 py-3.5 rounded-xl shadow-lg transition text-center text-sm">How It Works</a>
                </div>
            </div>
            <div class="lg:col-span-5">
                <div class="bg-slate-900 border border-slate-800 rounded-2xl p-4 sm:p-6 shadow-2xl">
                    <div class="flex justify-between items-center pb-3 mb-3 border-b border-slate-800">
                        <span class="text-xs font-bold text-slate-400 uppercase tracking-wider">Live System Pipeline</span>
                        <span class="text-[10px] font-mono text-emerald-400 bg-emerald-950 px-2 py-0.5 rounded-full border border-emerald-800">ACTIVE FEED</span>
                    </div>
                    <div class="space-y-2.5 font-mono text-xs">
                        <div class="p-3 bg-slate-950 rounded-lg border border-slate-800 flex justify-between items-center">
                            <div>
                                <div class="text-slate-200 font-bold">Building & Safety Citation</div>
                                <div class="text-[10px] text-slate-500">Commercial Parcel Indexed</div>
                            </div>
                            <span class="text-blue-400 font-bold text-[10px] bg-blue-950 px-2 py-1 rounded border border-blue-800">MATCHED</span>
                        </div>
                        <div class="p-3 bg-slate-950 rounded-lg border border-slate-800 flex justify-between items-center">
                            <div>
                                <div class="text-slate-200 font-bold">Municipal Tax Lien</div>
                                <div class="text-[10px] text-slate-500">Resolution Line Assigned</div>
                            </div>
                            <span class="text-emerald-400 font-bold text-[10px] bg-emerald-950 px-2 py-1 rounded border border-emerald-800">ROUTED</span>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </section>

    <!-- PROCESS SECTION -->
    <section id="how-it-works" class="py-16 sm:py-20 bg-slate-900 border-t border-slate-800">
        <div class="max-w-7xl mx-auto px-4 sm:px-6">
            <div class="text-center mb-12">
                <h2 class="text-xs font-bold text-blue-400 uppercase tracking-widest mb-2">Our Process</h2>
                <p class="text-2xl sm:text-3xl font-extrabold text-white">How We Assist Property Owners</p>
            </div>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
                <div class="bg-slate-950 p-6 rounded-2xl border border-slate-800">
                    <div class="text-blue-400 font-mono font-bold text-xs mb-2">STEP 01</div>
                    <h3 class="text-lg font-bold text-white mb-2">Record Monitoring</h3>
                    <p class="text-xs text-slate-400 leading-relaxed">System indexes municipal databases for active building citations and property notices across US jurisdictions.</p>
                </div>
                <div class="bg-slate-950 p-6 rounded-2xl border border-slate-800">
                    <div class="text-emerald-400 font-mono font-bold text-xs mb-2">STEP 02</div>
                    <h3 class="text-lg font-bold text-white mb-2">Owner Notification</h3>
                    <p class="text-xs text-slate-400 leading-relaxed">Property owners receive case notices displaying indexed parcel flags and filing details.</p>
                </div>
                <div class="bg-slate-950 p-6 rounded-2xl border border-slate-800">
                    <div class="text-amber-400 font-mono font-bold text-xs mb-2">STEP 03</div>
                    <h3 class="text-lg font-bold text-white mb-2">Direct Resolution</h3>
                    <p class="text-xs text-slate-400 leading-relaxed">Property owners connect directly with verified specialists who assist in resolving compliance issues.</p>
                </div>
            </div>
        </div>
    </section>

    <footer class="bg-slate-950 border-t border-slate-800 py-8 text-center text-xs text-slate-500">
        <p>&copy; 2026 EMERGENCYAUDIT.com!. All rights reserved.</p>
    </footer>

    <script>
      async function startAndreaWebCall() {
        try {
          const res = await fetch('/api/andrea-web-call', { method: 'POST' });
          const data = await res.json();
          if (data.access_token) {
            const retellClient = new RetellWebClient();
            await retellClient.startCall({ accessToken: data.access_token });
            alert('🎙️ Connecting call to Andrea AI Specialist... Ensure your microphone is enabled.');
          } else {
            alert('Andrea AI session token missing. Please dial the resolution line directly.');
          }
        } catch (e) {
          window.location.href = 'tel:18005550199';
        }
      }
    </script>
</body>
</html>`;
}

function getDashboardHTML(leads = [], isPaused = false) {
  const activeCount = leads.length;
  const unmaskedLeads = leads.filter(l => l.phone && l.phone !== 'PENDING UNMASK' && l.phone !== 'Unmasked Upon Purchase');
  const unmaskedCount = unmaskedLeads.length;
  const totalValue = activeCount * 250;

  const rowsHTML = leads.length > 0 ? leads.map((l, index) => {
    const phoneVal = l.phone || l.customerPhone || 'PENDING UNMASK';
    const emailVal = l.email || 'N/A';
    const ownerVal = l.owner_name || l.customerName || 'PROPERTY OWNER / MANAGER';
    const mailAddressVal = l.mail_address || l.address || 'ON FILE';
    const isUnmasked = phoneVal !== 'PENDING UNMASK' && phoneVal !== 'Unmasked Upon Purchase';
    const dropId = l.dropId || `job_${l.citation_id}`;
    const category = l.category || 'COMMERCIAL';
    const address = l.address || l.customerAddress || 'N/A';

    let displayCitationId = l.citation_id || 'AUD-8842';
    if (displayCitationId === 'AUD-48890' || !displayCitationId) {
      let hash = 0;
      for (let i = 0; i < address.length; i++) hash = (hash << 5) - hash + address.charCodeAt(i);
      displayCitationId = 'AUD-' + (Math.abs(hash) % 899999 + 100000);
    }

    let catBadgeClass = 'bg-blue-950 text-blue-400 border-blue-800';
    if (category === 'EMERGENCY') catBadgeClass = 'bg-red-950 text-red-400 border-red-800';
    if (category === 'TRADE') catBadgeClass = 'bg-amber-950 text-amber-400 border-amber-800';

    const caseUrl = l.case_url || `https://emergencyaudit.com/c/${displayCitationId}`;

    return `
      <tr onclick="toggleDrawer('${dropId}')" class="border-b border-slate-800 hover:bg-slate-800/60 transition font-mono text-[11px] sm:text-xs cursor-pointer">
        <td class="py-2.5 px-3 text-center" onclick="event.stopPropagation();">
          <input type="checkbox" class="lead-checkbox w-4 h-4 rounded bg-slate-900 border-slate-700 text-blue-600 focus:ring-0 cursor-pointer" value="${dropId}">
        </td>
        <td class="py-2.5 px-3 text-slate-500">${index + 1}</td>
        <td class="py-2.5 px-3 text-blue-400 font-bold whitespace-nowrap">${displayCitationId}</td>
        <td class="py-2.5 px-3 text-slate-200 font-semibold max-w-[180px] sm:max-w-xs truncate">${address}</td>
        <td class="py-2.5 px-3 text-slate-300 max-w-[120px] truncate">${ownerVal}</td>
        <td class="py-2.5 px-3 whitespace-nowrap">
          <span class="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold border ${catBadgeClass}">
            ${category}
          </span>
        </td>
        <td class="py-2.5 px-3 whitespace-nowrap">
          <span class="${isUnmasked ? 'text-emerald-400 font-bold' : 'text-slate-500'}">
            ${isUnmasked ? phoneVal : 'PENDING UNMASK'}
          </span>
        </td>
        <td class="py-2.5 px-3 whitespace-nowrap">
          <span class="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold ${l.status === 'SENT' ? 'bg-emerald-950 text-emerald-400 border border-emerald-800' : 'bg-blue-950 text-blue-400 border border-blue-800'}">
            ${l.status || 'INDEXED'}
          </span>
        </td>
      </tr>
      <tr id="drawer_${dropId}" class="hidden bg-slate-950 border-b border-slate-800">
        <td colspan="8" class="p-3 sm:p-4 font-mono text-xs">
          <div class="bg-slate-900 border border-slate-800 rounded-xl p-3 sm:p-4 space-y-3">
            <div class="text-amber-400 font-extrabold uppercase text-[11px] flex justify-between items-center">
              <span>📋 Enhanced Lead Dossier & Asset Specs</span>
              <span class="text-slate-500 text-[10px]">ID: ${displayCitationId}</span>
            </div>
            
            <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5 text-slate-300 text-[11px]">
              <div class="bg-slate-950 p-2.5 rounded-lg border border-slate-800">
                <span class="text-slate-500 block uppercase text-[9px] font-bold">Unmasked Owner Name</span>
                <strong class="text-amber-400 text-xs truncate block">${ownerVal}</strong>
              </div>
              <div class="bg-slate-950 p-2.5 rounded-lg border border-slate-800">
                <span class="text-slate-500 block uppercase text-[9px] font-bold">Unmasked Mobile</span>
                <strong class="text-emerald-400 text-xs">${phoneVal}</strong>
              </div>
              <div class="bg-slate-950 p-2.5 rounded-lg border border-slate-800">
                <span class="text-slate-500 block uppercase text-[9px] font-bold">Owner Email Append</span>
                <strong class="text-blue-400 text-xs truncate block">${emailVal}</strong>
              </div>
              <div class="bg-slate-950 p-2.5 rounded-lg border border-slate-800">
                <span class="text-slate-500 block uppercase text-[9px] font-bold">Tax Mailing Address</span>
                <strong class="text-slate-200 truncate block">${mailAddressVal}</strong>
              </div>
              <div class="bg-slate-950 p-2.5 rounded-lg border border-slate-800">
                <span class="text-slate-500 block uppercase text-[9px] font-bold">Parcel APN</span>
                <strong class="text-slate-200">${l.apn || 'ON FILE'}</strong>
              </div>
              <div class="bg-slate-950 p-2.5 rounded-lg border border-slate-800">
                <span class="text-slate-500 block uppercase text-[9px] font-bold">Square Footage / Year</span>
                <strong class="text-slate-200">${l.sqft || 'N/A'} • Built ${l.year_built || 'N/A'}</strong>
              </div>
              <div class="bg-slate-950 p-2.5 rounded-lg border border-slate-800 sm:col-span-2 lg:col-span-3">
                <span class="text-slate-500 block uppercase text-[9px] font-bold">Zoning / Use Code</span>
                <strong class="text-slate-200">${l.zoning || 'N/A'} • ${l.property_use || 'REAL ESTATE'}</strong>
              </div>
            </div>

            <div class="pt-2 border-t border-slate-800 text-[11px] text-slate-300">
              <span class="text-slate-500 font-bold block uppercase text-[9px] mb-0.5">Municipal Violation Text</span>
              <p class="bg-slate-950 p-2.5 rounded-lg border border-slate-800 text-amber-200 leading-relaxed break-words">
                ${l.violation || l.desc_en || 'Municipal Order Recorded'}
              </p>
            </div>

            <div class="pt-2 flex flex-col sm:flex-row justify-between items-start sm:items-center gap-2 text-[10px] text-slate-400">
              <div class="truncate max-w-full">
                <strong>Generated Door 2 URL:</strong> 
                <a href="${caseUrl}" target="_blank" class="text-blue-400 underline break-all">${caseUrl}</a>
              </div>
            </div>
          </div>
        </td>
      </tr>
    `;
  }).join('') : `
    <tr>
      <td colspan="8" class="py-12 text-center text-slate-500 font-mono text-xs">
        No leads stored in memory. Run your Python scraper workflow to populate fresh records.
      </td>
    </tr>
  `;

  return `<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>EMERGENCYAUDIT.com! | Enterprise Command Center</title>
    <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' rx='20' fill='%231e3a8a'/><text x='50%' y='68%' font-size='55' font-weight='900' fill='white' text-anchor='middle' font-family='sans-serif'>EA</text></svg>">
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
    <style> body { font-family: 'Inter', sans-serif; } </style>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen antialiased">
    <header class="border-b border-slate-800 bg-slate-900/90 backdrop-blur-md sticky top-0 z-50 px-3 sm:px-6 py-3">
        <div class="max-w-7xl mx-auto flex flex-wrap items-center justify-between gap-3">
            <div class="flex items-center space-x-2.5">
                <div class="bg-blue-600 text-white font-black px-2.5 py-1 rounded text-sm tracking-wider">EA</div>
                <div>
                    <span class="font-extrabold text-sm sm:text-base tracking-tight text-blue-500 block leading-none">EMERGENCY<span class="text-white">AUDIT</span><span class="text-blue-500">.com!</span></span>
                    <span class="text-[9px] font-mono text-slate-400 uppercase block mt-0.5">Back Office Command Engine v3.5</span>
                </div>
            </div>
            <div class="flex items-center space-x-2 sm:space-x-4">
                <span class="hidden sm:flex items-center space-x-2 text-xs font-mono bg-slate-800 px-3 py-1.5 rounded-full border border-slate-700">
                    <span class="w-2 h-2 rounded-full ${isPaused ? 'bg-amber-400' : 'bg-emerald-400 animate-pulse'}"></span>
                    <span class="text-slate-300">${isPaused ? 'PAUSED' : 'ACTIVE'}</span>
                </span>
                <button onclick="togglePause()" class="${isPaused ? 'bg-emerald-600 hover:bg-emerald-500' : 'bg-amber-600 hover:bg-amber-500'} text-white font-bold text-xs px-3.5 py-2 rounded-lg transition shadow-md whitespace-nowrap">
                    ${isPaused ? '▶️ Resume Pipeline' : '⏸️ Pause Pipeline'}
                </button>
            </div>
        </div>
    </header>

    <main class="max-w-7xl mx-auto px-3 sm:px-6 py-6 sm:py-8">
        <!-- METRICS GRID -->
        <div class="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4 mb-6">
            <div class="bg-slate-900 border border-slate-800 rounded-xl p-3.5 sm:p-5 shadow-lg">
                <span class="text-slate-400 text-[10px] sm:text-xs font-mono uppercase block mb-1">Indexed Citations</span>
                <span class="text-2xl sm:text-3xl font-black text-white font-mono">${activeCount}</span>
            </div>
            <div class="bg-slate-900 border border-slate-800 rounded-xl p-3.5 sm:p-5 shadow-lg">
                <span class="text-slate-400 text-[10px] sm:text-xs font-mono uppercase block mb-1">Cell Unmasked</span>
                <span class="text-2xl sm:text-3xl font-black text-emerald-400 font-mono">${unmaskedCount}</span>
            </div>
            <div class="bg-slate-900 border border-slate-800 rounded-xl p-3.5 sm:p-5 shadow-lg">
                <span class="text-slate-400 text-[10px] sm:text-xs font-mono uppercase block mb-1">Door 2 Traffic</span>
                <span class="text-2xl sm:text-3xl font-black text-blue-400 font-mono">${activeCount}</span>
            </div>
            <div class="bg-slate-900 border border-slate-800 rounded-xl p-3.5 sm:p-5 shadow-lg">
                <span class="text-slate-400 text-[10px] sm:text-xs font-mono uppercase block mb-1">Est. Value</span>
                <span class="text-2xl sm:text-3xl font-black text-amber-400 font-mono">$${totalValue.toLocaleString()}</span>
            </div>
        </div>

        <!-- ACTION CONTROLS BAR -->
        <div class="bg-slate-900 border border-slate-800 rounded-xl p-4 sm:p-6 shadow-lg mb-6">
            <div class="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4 pb-4 border-b border-slate-800">
                <div>
                    <h2 class="text-sm sm:text-base font-extrabold text-white">Pipeline Execution & Dispatch Controls</h2>
                    <p class="text-xs text-slate-400">Manage scrapers, clean up stale records, and trigger batch releases.</p>
                </div>
                <div class="flex flex-wrap gap-2 w-full lg:w-auto">
                    <button onclick="releaseStaged()" class="flex-1 lg:flex-none bg-blue-600 hover:bg-blue-500 text-white font-bold text-[11px] sm:text-xs px-3 py-2 rounded-lg transition shadow-md whitespace-nowrap">
                        🚀 Release Staged
                    </button>
                    <button onclick="purgeUnmaskedLeads()" class="flex-1 lg:flex-none bg-amber-950 hover:bg-amber-900 text-amber-400 border border-amber-800 font-bold text-[11px] sm:text-xs px-3 py-2 rounded-lg transition shadow-md whitespace-nowrap">
                        🧹 Sweep Dead Leads
                    </button>
                    <button onclick="deleteSelected()" class="flex-1 lg:flex-none bg-red-950 hover:bg-red-900 text-red-400 border border-red-800 font-bold text-[11px] sm:text-xs px-3 py-2 rounded-lg transition whitespace-nowrap">
                        🗑️ Delete Selected
                    </button>
                    <button onclick="purgeAllLeads()" class="flex-1 lg:flex-none bg-slate-800 hover:bg-slate-700 text-red-400 border border-slate-700 font-semibold text-[11px] sm:text-xs px-3 py-2 rounded-lg transition whitespace-nowrap">
                        ⚠️ Purge KV
                    </button>
                    <button onclick="location.reload()" class="flex-1 lg:flex-none bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 font-semibold text-[11px] sm:text-xs px-3 py-2 rounded-lg transition whitespace-nowrap">
                        🔄 Refresh
                    </button>
                </div>
            </div>
        </div>

        <!-- DATA LEDGER TABLE -->
        <div class="bg-slate-900 border border-slate-800 rounded-xl shadow-lg overflow-hidden">
            <div class="p-4 sm:p-6 border-b border-slate-800 flex justify-between items-center gap-2">
                <div>
                    <h2 class="text-sm sm:text-base font-extrabold text-white">Processed Citation Data Ledger</h2>
                    <p class="text-xs text-slate-400">Click any row to open the complete owner dossier and property specs.</p>
                </div>
                <span class="text-xs font-mono text-slate-400 bg-slate-800 px-2.5 py-1 rounded-md border border-slate-700 whitespace-nowrap">
                    ${activeCount} Leads
                </span>
            </div>
            
            <div class="overflow-x-auto">
                <table class="w-full text-left border-collapse min-w-[700px]">
                    <thead>
                        <tr class="bg-slate-950 border-b border-slate-800 font-mono text-[10px] sm:text-[11px] text-slate-400 uppercase">
                            <th class="py-3 px-3 text-center w-10">
                              <input type="checkbox" id="selectAll" onclick="toggleSelectAll(this)" class="w-4 h-4 rounded bg-slate-900 border-slate-700 text-blue-600 focus:ring-0 cursor-pointer">
                            </th>
                            <th class="py-3 px-3 w-8">#</th>
                            <th class="py-3 px-3">Citation ID</th>
                            <th class="py-3 px-3">Property Address</th>
                            <th class="py-3 px-3">Owner / Entity</th>
                            <th class="py-3 px-3">Category</th>
                            <th class="py-3 px-3">Unmasked Cell</th>
                            <th class="py-3 px-3">Status</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${rowsHTML}
                    </tbody>
                </table>
            </div>
        </div>
    </main>

    <script>
        function toggleDrawer(dropId) {
            const drawer = document.getElementById('drawer_' + dropId);
            if (drawer) drawer.classList.toggle('hidden');
        }
        function toggleSelectAll(master) {
            const checkboxes = document.querySelectorAll('.lead-checkbox');
            checkboxes.forEach(cb => cb.checked = master.checked);
        }
        async function togglePause() {
            await fetch('/api/admin/toggle-pause', { method: 'POST' });
            location.reload();
        }
        async function releaseStaged() {
            if (!confirm('Approve and mark all PENDING_REVIEW staged leads ready for dispatch?')) return;
            const res = await fetch('/api/admin/release-staged', { method: 'POST' });
            const data = await res.json();
            alert('Released ' + (data.releasedCount || 0) + ' staged leads.');
            location.reload();
        }
        async function purgeUnmaskedLeads() {
            if (!confirm('Sweep and delete all PENDING UNMASK and dead leads from memory?')) return;
            const res = await fetch('/api/admin/purge-unmasked', { method: 'POST' });
            const data = await res.json();
            alert('Swept ' + (data.purgedCount || 0) + ' dead leads.');
            location.reload();
        }
        async function deleteSelected() {
            const selected = Array.from(document.querySelectorAll('.lead-checkbox:checked')).map(cb => cb.value);
            if (selected.length === 0) {
                alert('Please select at least one lead to delete.');
                return;
            }
            if (!confirm('Delete ' + selected.length + ' selected leads?')) return;
            await fetch('/api/admin/delete-batch', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ dropIds: selected })
            });
            location.reload();
        }
        async function purgeAllLeads() {
            if (!confirm('⚠️ PURGE ALL LEADS FROM CLOUDFLARE KV?')) return;
            await fetch('/api/admin/purge-all', { method: 'POST' });
            location.reload();
        }
    </script>
</body>
</html>`;
}
