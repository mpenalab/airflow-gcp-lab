
from airflow import DAG
from airflow.models import Variable
from airflow.providers.google.cloud.operators.pubsub import PubSubPullOperator
from airflow.providers.google.cloud.transfers.gcs_to_bigquery import GCSToBigQueryOperator
from airflow.providers.google.cloud.operators.bigquery import BigQueryInsertJobOperator
from airflow.providers.google.cloud.hooks.gcs import GCSHook
from airflow.operators.python import PythonOperator
from airflow.utils.dates import days_ago
from datetime import timedelta
import json
import base64
import tempfile
import os


PROJECT_ID = Variable.get("GCP_PROJECT_ID", default_var="tu-proyecto-id")
SUBSCRIPTION_ID = "sales-sub"
BUCKET_NAME = Variable.get("GCS_BUCKET_NAME", default_var="datapath-sales-bucket")
DATASET_ID = "sales_dwh"
TABLE_RAW = "sales_raw"
TABLE_AGG = "sales_summary"

default_args = {
    'owner': 'datapath',
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

with DAG(
    'sales_pipeline_gcp',
    default_args=default_args,
    schedule_interval='@hourly',
    start_date=days_ago(1),
    tags=['lab_intermedio', 'gcp', 'datapath'],
    catchup=False
) as dag:

    pull_messages = PubSubPullOperator(
        task_id='pull_pubsub_messages',
        project_id=Variable.get("GCP_PROJECT_ID"), # <--- Asegúrate que diga project_id
        subscription='sales-sub',
        max_messages=10,
        gcp_conn_id='google_cloud_default'
    )

    def process_and_upload_to_gcs(ti):
        pulled_messages = ti.xcom_pull(task_ids='pull_pubsub_messages', key='return_value')
        
        if not pulled_messages:
            print("No messages found from Pub/Sub.")
            return 'no_messages'
            
        processed_records = []
        for msg in pulled_messages:
            data_encoded = msg.get('message', {}).get('data', '')
            if data_encoded:
                data_decoded = base64.b64decode(data_encoded).decode('utf-8')
                record = json.loads(data_decoded)
                processed_records.append(record)
        
        if not processed_records:
            return 'no_valid_records'

        ndjson_content = '\n'.join([json.dumps(r) for r in processed_records])
        
        filename = f"sales/raw/sales_{ti.execution_date.strftime('%Y%m%d_%H%M%S')}.json"
        
        gcs_hook = GCSHook(gcp_conn_id='google_cloud_default')
        gcs_hook.upload(
            bucket_name=BUCKET_NAME,
            object_name=filename,
            data=ndjson_content,
            mime_type='application/json'
        )
        
        return filename

    process_task = PythonOperator(
        task_id='process_and_upload_gcs',
        python_callable=process_and_upload_to_gcs
    )

    load_to_bq = GCSToBigQueryOperator(
        task_id='load_to_bq_raw',
        bucket=BUCKET_NAME,
        source_objects=["{{ task_instance.xcom_pull(task_ids='process_and_upload_gcs') }}"],
        destination_project_dataset_table=f"{PROJECT_ID}.{DATASET_ID}.{TABLE_RAW}",
        source_format='NEWLINE_DELIMITED_JSON',
        write_disposition='WRITE_APPEND',
        autodetect=True,
        create_disposition='CREATE_IF_NEEDED'
    )

    sql_agg = f"""
    CREATE OR REPLACE TABLE `{PROJECT_ID}.{DATASET_ID}.{TABLE_AGG}` AS
    SELECT
        product_id,
        DATE(timestamp) as sale_date,
        COUNT(*) as total_orders,
        SUM(quantity) as total_quantity,
        SUM(total_amount) as total_revenue
    FROM `{PROJECT_ID}.{DATASET_ID}.{TABLE_RAW}`
    GROUP BY 1, 2
    ORDER BY 2 DESC, 5 DESC;
    """

    transform_bq = BigQueryInsertJobOperator(
        task_id='transform_summary_table',
        configuration={
            "query": {
                "query": sql_agg,
                "useLegacySql": False,
            }
        }
    )

    pull_messages >> process_task >> load_to_bq >> transform_bq
