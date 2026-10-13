export class CallRoom {
  constructor(private state: DurableObjectState) {}
  async fetch(req: Request) {
    const url = new URL(req.url);
    if (url.pathname === '/cleanup') await this.state.storage.deleteAll();
    return new Response('ok');
  }
}
