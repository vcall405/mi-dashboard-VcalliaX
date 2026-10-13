export async function getClient(env: any, id: string) {
  return env.DB.prepare('SELECT * FROM clientes WHERE id = ?').bind(id).first();
}
export async function saveCall(env: any, call: any) {
  await env.DB.prepare('INSERT INTO llamadas (id, payload) VALUES (?, ?)').bind(call.id, JSON.stringify(call)).run();
}
