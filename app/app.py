from flask import Flask, request, jsonify
from celery import Celery, signals
import subprocess
import os
import json
import redis
from celery.exceptions import MaxRetriesExceededError

app = Flask(__name__)

# Konfigurasi Flask untuk Celery
app.config.update(
    CELERY_BROKER_URL='pyamqp://guest@rabbitmq//',
    CELERY_RESULT_BACKEND='rpc://'
)

# Inisialisasi Celery
celery = Celery(app.import_name, backend='rpc://', broker='pyamqp://guest@rabbitmq//')

# Inisialisasi Redis
redis_client = redis.StrictRedis(host='python-sqlmap-redis', port=6380, db=0, decode_responses=True)

# Max task concurrency
MAX_CONCURRENT_TASKS = 2

def enqueue_task(task_id):
    redis_client.rpush('task_queue', task_id)

def dequeue_task():
    return redis_client.lpop('task_queue')

def update_task_status(task_id, status, progress=None):
    redis_client.hset(f'task:{task_id}', mapping={'status': status, 'progress': progress or '0%'})

def get_task_status(task_id):
    return redis_client.hgetall(f'task:{task_id}')

def task_can_run():
    active_tasks = redis_client.llen('active_tasks')
    return active_tasks < MAX_CONCURRENT_TASKS

def add_active_task(task_id):
    redis_client.rpush('active_tasks', task_id)

def remove_active_task(task_id):
    redis_client.lrem('active_tasks', 0, task_id)

# Celery task to run SQLMap scan
@celery.task(bind=True, name="app.run_sqlmap")
def run_sqlmap(self, target_url, sqlmap_params=None):
    task_id = self.request.id
    update_task_status(task_id, 'RUNNING')
    add_active_task(task_id)
    
    try:
        sqlmap_args = ['python3', '/sqlmap/sqlmap.py', '--url', target_url, '--batch']
        if sqlmap_params:
            sqlmap_args.extend(sqlmap_params.split())
        
        process = subprocess.Popen(sqlmap_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        lines = []
        for line in process.stdout:
            line = line.strip()
            lines.append(line)
            progress = f'{len(lines)} lines processed'
            update_task_status(task_id, 'RUNNING', progress)

        process.wait()
        result_status = 'COMPLETED' if process.returncode == 0 else 'FAILED'
        update_task_status(task_id, result_status)
        with open(f'./results/{task_id}.txt', 'w') as f:
            f.write('\n'.join(lines))
    except Exception as e:
        update_task_status(task_id, 'FAILED', str(e))
        raise
    finally:
        remove_active_task(task_id)
        next_task_id = dequeue_task()
        if next_task_id:
            run_sqlmap.apply_async(task_id=next_task_id)

@app.route('/start-scan', methods=['POST'])
def start_scan():
    data = request.json
    target_url = data.get('target_url')
    sqlmap_params = data.get('sqlmap_params', '')

    if not target_url:
        return jsonify({'error': 'target_url is required'}), 400

    task = run_sqlmap.apply_async(args=[target_url, sqlmap_params])
    task_id = task.id
    update_task_status(task_id, 'QUEUED')
    
    if task_can_run():
        run_sqlmap.apply_async(args=[target_url, sqlmap_params], task_id=task_id)
    else:
        enqueue_task(task_id)

    return jsonify({'message': 'Scan queued or started', 'task_id': task_id})

@app.route('/scan-status/<task_id>', methods=['GET'])
def scan_status(task_id):
    task_data = get_task_status(task_id)
    return jsonify(task_data), 200

@app.route('/tasks', methods=['GET'])
def get_all_tasks():
    queued_tasks = redis_client.lrange('task_queue', 0, -1)
    active_tasks = redis_client.lrange('active_tasks', 0, -1)
    return jsonify({
        'queued': queued_tasks,
        'active': active_tasks,
        'completed': redis_client.keys('task:*:status:COMPLETED'),
        'failed': redis_client.keys('task:*:status:FAILED')
    })

@app.route('/scan-result/<task_id>', methods=['GET'])
def scan_result(task_id):
    file_path = f'./results/{task_id}.txt'
    if os.path.exists(file_path):
        with open(file_path, 'r') as f:
            result_content = f.read()
        return jsonify({'task_id': task_id, 'result': result_content}), 200
    else:
        return jsonify({'error': 'Result file not found'}), 404


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
