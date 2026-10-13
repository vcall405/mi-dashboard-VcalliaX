import { Hono } from 'hono';
import { saveCall, getClient } from './db';
import { notifyN8n } from './n8n';
export { CallRoom } from './room';

type Env = { DB: D1Database; SESSIONS: KVNamespace; RECORDINGS: R2Bucket; CALL_QUEUE: Queue; ROOMS: DurableObjectNamespace; VAPI_SECRET: string; N8N_WEBHOOK_URL: string };
const app = new Hono<{ Bindings: Env }>();

app.post('/vapi/webhook', async (c) => {
  if (c.req.header('X-Vapi-Secret') !== c.env.VAPI_SECRET) return c.text('no', 401);
  const body = await c.req.json();
  await c.env.CALL_QUEUE.send(body);
  return c.json({ ok: true });
});

app.get('/clients/:id', async (c) => {
  const client = await getClient(c.env, c.req.param('id'));
  const cached = await c.env.SESSIONS.get(client.phone);
  return c.json({ client, cached });
});

app.put('/recordings/:id', async (c) => {
  await c.env.RECORDINGS.put(c.req.param('id'), c.req.raw.body);
  return c.json({ ok: true });
});

export default {
  fetch: app.fetch,
  async queue(batch: MessageBatch, env: Env) {
    for (const msg of batch.messages) {
      await saveCall(env, msg.body);
      await notifyN8n(env, msg.body);
    }
  },
  async scheduled(event: ScheduledEvent, env: Env) {
    const id = env.ROOMS.idFromName('cleanup');
    await env.ROOMS.get(id).fetch('https://room/cleanup');
  },
};
