const express = require('express');
const { PubSub } = require('@google-cloud/pubsub');
const { Firestore } = require('@google-cloud/firestore');
const app = express();
const pubsub = new PubSub();
const db = new Firestore();

async function saveLead(lead) {
  await db.collection('leads').add(lead);
}

async function publishLead(lead) {
  await pubsub.topic('new-leads').publishMessage({ json: lead });
}

app.post('/webhook/meta', async (req, res) => {
  if (req.query['hub.verify_token'] !== process.env.META_VERIFY_TOKEN) return res.sendStatus(403);
  await saveLead(req.body);
  await publishLead(req.body);
  res.sendStatus(200);
});

app.get('/leads', async (req, res) => {
  const snap = await db.collection('leads').limit(50).get();
  res.json(snap.docs.map((d) => d.data()));
});

app.listen(process.env.PORT || 8080);
