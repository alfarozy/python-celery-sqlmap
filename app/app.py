from flask import Flask, request, jsonify
from celery import Celery, signals
import subprocess
import os
import json
from celery.app.control import Inspect

app = Flask(__name__)

# Konfigurasi Flask untuk Celery
app.config.update(
    CELERY_BROKER_URL='pyamqp://guest@rabbitmq//',
    CELERY_RESULT_BACKEND='rpc://'
)

# Inisialisasi Celery
celery = Celery(
    app.import_name,
    backend='rpc://',
    broker='pyamqp://guest@rabbitmq//'
)

# Inisialisasi dictionary untuk menyimpan status task
task_status = {
    'successful': [],
    'failed': []
}

# Celery task to run SQLMap scan
@celery.task(bind=True, name="app.run_sqlmap")
def run_sqlmap(self, target_url, sqlmap_params=None):
    try:
        # Perbarui status awal
        self.update_state(state="PROGRESS", meta={"progress": "Starting SQLMap scan..."})

        # Tentukan file hasil JSON dan log
        task_id = self.request.id
       
        # Default parameter: --batch (non-interactive mode)
        sqlmap_args = ['python3', '/sqlmap/sqlmap.py', '--url', target_url, '--batch']
        
        if sqlmap_params:
            if isinstance(sqlmap_params, str):
                sqlmap_params = sqlmap_params.split()
            sqlmap_args.extend(sqlmap_params)

        # Jalankan SQLMap
        process = subprocess.Popen(
            sqlmap_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        # Inisialisasi untuk menghitung total baris output
        total_lines = 0
        lines_processed = 0

        # Buat daftar kosong untuk menampung baris output
        lines = []

        # Tulis setiap baris output ke file JSON dan log, serta hitung progres
        for line in process.stdout:
            line = line.strip()  # Hapus spasi kosong

            # Tambahkan baris ke daftar lines
            lines.append(line)

             # Tentukan path file hasil
            file_path = f'./results/{task_id}.txt'
            os.makedirs(os.path.dirname(file_path), exist_ok=True)  # Buat direktori jika belum ada

            # Simpan hasil output ke dalam file teks
            with open(file_path, 'w') as file:
                file.write("\n".join(lines))  # Gabungkan baris menjadi teks dan tulis ke file
            print(f"Task result saved to {file_path}")
            
            # Hitung persentase progres
            lines_processed += 1
            if total_lines == 0:
                total_lines = 1  # Jangan biarkan total_lines 0 untuk menghindari pembagian dengan 0

            progress_percentage = int((lines_processed / total_lines))

            # Update progress (percentage)
            self.update_state(
                state="PROGRESS", 
                meta={
                    "progress": f"{progress_percentage}%",
                    'logs':lines
                }
            )
        # Tunggu proses selesai
        process.wait()

        # Setelah proses selesai, simpan hasil ke file JSON
        if process.returncode == 0:
            output = {"status": "Completed", "log_file": lines}
        else:
            output = {"status": "Failed", "error": process.stderr.read().strip(), "log_file": lines}

        return output

    except Exception as e:
        return {"status": "Failed", "error": str(e)}

@app.route('/')
def index():
    return "SQLMap API is running!"

# Route to start scan
@app.route('/start-scan', methods=['POST'])
def start_scan():
    data = request.json
    target_url = data.get('target_url')
    sqlmap_params = data.get('sqlmap_params', [])  # Get SQLMap parameters, default to an empty list

    if not target_url:
        return jsonify({'error': 'target_url is required'}), 400

    # Memulai scan SQLMap dengan Celery
    task = run_sqlmap.apply_async(args=[target_url, sqlmap_params])
    return jsonify({'message': 'Scan started', 'task_id': task.id}), 202



@app.route('/scan-status/<task_id>', methods=['GET'])
def scan_status(task_id):
    task = run_sqlmap.AsyncResult(task_id)
    # Menyiapkan response dengan status task
    response = {
        'task_id': task_id,
        'status': task.status,  # Status task (PROGRESS, SUCCESS, FAILURE)
        'progress': 'No progress available',
        'result' : task.result
    }

    # If task info is available, get the progress
    if task.info:
        response['progress'] = task.info.get('progress', 'No progress available')

    return jsonify(response)

# Fungsi untuk menyimpan hasil task ke dalam file
def save_task_result(task_id, result):
    file_path = f'./logs/{task_id}.json'
    os.makedirs(os.path.dirname(file_path), exist_ok=True)  # Buat direktori jika belum ada
    
    with open(file_path, 'w') as file:
        json.dump(result, file, indent=4)
    print(f"Task result saved to {file_path}")

# Menangani task sukses dan menyimpan hasil
@signals.task_success.connect
def task_success_handler(sender=None, result=None, **kwargs):
    task_id = sender.request.id
    print(f"Task {task_id} completed successfully.")
    
    # Simpan hasil task ke file
    save_task_result(task_id, result)
    
    # Tambahkan task ke daftar 'successful'
    task_status['successful'].append(task_id)

# Menangani task gagal dan mencoba menjadwalkannya ulang
@signals.task_failure.connect
def task_failure_handler(sender=None, exception=None, **kwargs):
    task_id = sender.request.id
    print(f"Task {task_id} failed. Retrying...")
    
    # Tambahkan task ke daftar 'failed'
    task_status['failed'].append(task_id)
    
    # Menjadwalkan ulang task yang gagal
    sender.retry(countdown=10, max_retries=5)

@app.route('/tasks', methods=['GET'], endpoint='get_all_tasks')
def get_all_tasks():
    # Inisialisasi Celery inspect
    i = Inspect(app=celery)

    # Ambil daftar tugas aktif, antrian, dan terjadwal
    active_tasks = i.active() or {}
    scheduled_tasks = i.reserved() or {}

    # Struktur respons
    response = {
        'active': active_tasks,
        'scheduled': scheduled_tasks,
        'successful': task_status['successful'],
        'failed': task_status['failed']
    }

    return jsonify(response), 200


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
