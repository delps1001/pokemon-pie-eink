#!/bin/bash

# Configuration
PI_USER="pi"
PI_HOST="192.168.1.133"  # Change this to your Pi's IP address if needed
PI_DESTINATION="/home/pi/pokemon-calendar/"

while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            echo "Usage: $0 [options]"
            echo "Options:"
            echo "  --help          Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Files to copy
FILES=(
  "docker-compose.yml"
  "install_e_ink_docker.sh"
)

# Directories to copy
DIRECTORIES=(
)

# Use SSH multiplexing to reuse connections
SSH_OPTS="-o ControlMaster=auto -o ControlPath=/tmp/ssh_%r@%h:%p -o ControlPersist=10m"

echo "🚀 Deploying Pokemon Calendar to Raspberry Pi"
echo "================================================"

# Create destination directory on Pi if it doesn't exist
echo "📁 Creating destination directory on Pi..."
ssh ${SSH_OPTS} ${PI_USER}@${PI_HOST} "mkdir -p ${PI_DESTINATION}"

# Copy all files in a single scp command to reuse the connection
echo "📄 Copying files to Pi..."
FILES_TO_COPY=""
for file in "${FILES[@]}"; do
    if [ -f "$file" ]; then
        FILES_TO_COPY="$FILES_TO_COPY $file"
    else
        echo "⚠️  Warning: $file not found in current directory"
    fi
done

if [ -n "$FILES_TO_COPY" ]; then
    scp ${SSH_OPTS} $FILES_TO_COPY ${PI_USER}@${PI_HOST}:${PI_DESTINATION}
    echo "✅ Files copied successfully"
else
    echo "❌ No files to copy!"
fi

for dir in "${DIRECTORIES[@]}"; do
    echo ""
    echo "Processing directory: $dir"
    echo "------------------------"
done

echo ""
echo "================================================"
echo "🎉 Deployment complete!"