import os
import functions_framework
import requests
from google.cloud import storage


def send_whatsapp(phone, text):
    token = os.environ["META_TOKEN"]
    requests.post("https://graph.facebook.com/v21.0/123/messages", headers={"Authorization": f"Bearer {token}"}, json={"to": phone, "text": {"body": text}})


@functions_framework.cloud_event
def notify_lead(cloud_event):
    lead = cloud_event.data
    send_whatsapp(lead["phone"], "Hola!")
    storage.Client().bucket("lead-archive").blob(lead["id"]).upload_from_string(str(lead))
