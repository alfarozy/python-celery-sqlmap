FROM python:3.9-slim

# Set working directory in container
WORKDIR /app

# Copy requirements.txt and install dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt
# Install SQLMap dependencies
RUN apt-get update && apt-get install -y git
RUN git clone --depth 1 https://github.com/sqlmapproject/sqlmap.git /sqlmap

# Create directories for results and logs, and set permissions
RUN mkdir -p /app/results /app/logs
RUN chmod -R 775 /app/results /app/logs
RUN chown -R 1000:1000 /app/results /app/logs

# Copy the application code into the container
COPY . /app/

ENV PATH="/usr/local/bin:$PATH"

# Expose the port for Flask app
EXPOSE 5000 5555

# Default command to run Flask app
CMD ["python3", "app/app.py"]
