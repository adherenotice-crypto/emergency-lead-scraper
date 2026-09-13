// Inside your Cloudflare Worker router for /api/ping
if (url.pathname === '/api/ping' && request.method === 'POST') {
  try {
    const data = await request.json();
    
    // Generate SKU or use provided one
    const sku = data.sku || `EA-AUTO-${data.zip || '90210'}-${Math.floor(1000 + Math.random() * 9000)}`;
    const dropId = data.dropId || `job_${Date.now()}_${Math.floor(100 + Math.random() * 900)}`;

    const workOrder = {
      sku,
      dropId,
      partnerId: data.partnerId || 'autonomous_dispatch_engine',
      category: data.category || 'HANDYMAN',
      title_en: data.title_en || 'Emergency Home Service Request',
      title_es: data.title_es || 'Solicitud de servicio de emergencia',
      zip: data.zip || '90210',
      city: data.city || 'Los Angeles, CA',
      desc_en: data.desc_en || 'Immediate contractor dispatch required.',
      desc_es: data.desc_es || 'Se requiere despacho inmediato de contratista.',
      wholesaleCost: 0.00,
      retailPrice: 25.00,
      customerName: 'Verified Homeowner',
      customerPhone: 'Unlocked Upon Purchase',
      customerAddress: data.customerAddress || 'Verified Service Location',
      status: 'AVAILABLE',
      createdAt: new Date().toISOString()
    };

    // Save to KV or Database binding
    await EMERGENCY_KV.put(sku, JSON.stringify(workOrder));

    return new Response(JSON.stringify({ success: true, sku }), {
      headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    });
  } catch (err) {
    return new Response(JSON.stringify({ success: false, error: err.message }), {
      status: 500,
      headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    });
  }
}
