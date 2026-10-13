export async function notifyN8n(env: any, payload: unknown) {
  await fetch(env.N8N_WEBHOOK_URL, { method: 'POST', body: JSON.stringify(payload) });
  await fetch('https://api.resend.com/emails', { method: 'POST', headers: { Authorization: `Bearer ${env.RESEND_API_KEY}` } });
}
