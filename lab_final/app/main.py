
import os
import json
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from google.cloud import pubsub_v1
from datetime import datetime
from typing import Optional

app = FastAPI(title="Sales Producer API")

PROJECT_ID = os.getenv("GCP_PROJECT_ID", "tu-proyecto-test")
TOPIC_ID = os.getenv("PUBSUB_TOPIC_ID", "sales-topic")

publisher = pubsub_v1.PublisherClient()
topic_path = publisher.topic_path(PROJECT_ID, TOPIC_ID)

class Order(BaseModel):
    order_id: int
    customer_id: int
    product_id: int
    quantity: int
    unit_price: float
    timestamp: Optional[str] = None

@app.get("/")
def health_check():
    return {"status": "ok", "service": "Sales Producer API"}

@app.post("/order")
async def create_order(order: Order):
    try:
        if not order.timestamp:
            order.timestamp = datetime.now().isoformat()
        
        total_amount = order.quantity * order.unit_price
        
        payload = order.dict()
        payload['total_amount'] = total_amount
        
        message_json = json.dumps(payload).encode("utf-8")
        
        future = publisher.publish(topic_path, message_json)
        message_id = future.result()
        
        return {
            "status": "published", 
            "message_id": message_id,
            "data": payload
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
