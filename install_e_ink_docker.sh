#!/bin/bash
# Remote Raspberry Pi Zero W 2 Deployment Script
# Run this on your nephew's Pi to set up the Pokemon Calendar

set -e

INSTALL_DIR="/home/pi/pokemon-calendar"

# Check if running on Pi
if ! grep -q "Raspberry Pi" /proc/cpuinfo 2>/dev/null; then
    echo "⚠️  Warning: Not running on Raspberry Pi"
    read -p "Continue anyway? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi


install_docker() {
    echo "🐳 Installing Docker..."

    # Update system
    sudo apt update

    # Install Docker using convenience script (recommended for Pi)
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh

    # Add pi user to docker group
    sudo usermod -aG docker pi

    # Install Docker Compose
    sudo apt install -y docker-compose

    # Enable Docker service
    sudo systemctl enable docker
    sudo systemctl start docker

    # Clean up
    rm get-docker.sh

    echo "✅ Docker installed successfully"
}

# Function to enable SPI for e-ink display
enable_spi() {
    echo "⚡ Enabling SPI interface..."
    sudo raspi-config nonint do_spi 0
    echo "✅ SPI enabled"
}

setup_application() {
    echo "📂 Setting up application..."

    # Create configuration for nephew's location
    cat > config.json << 'EOF'
{
    "display": {
        "type": "7in5_HD",
        "width": 880,
        "height": 528,
        "color_mode": "monochrome",
        "epaper_safety": {
            "min_refresh_interval_seconds": 180,
            "max_hours_without_refresh": 24
        }
    },
    "pokemon": {
        "start_pokemon_id": 1,
        "start_date": "2025-08-10",
        "cycle_all_pokemon": true
    },
    "cache": {
        "directory": "./pokemon_cache"
    },
    "demo": {
        "enabled": false
    },
    "image_processing": {
        "dithering_algorithm": "floyd_steinberg"
    }
}
EOF

    echo "✅ Application configured"
}


create_systemd_service() {
    echo "🔧 Creating systemd service..."

    sudo tee /etc/systemd/system/pokemon-calendar.service > /dev/null << EOF
[Unit]
Description=Pokemon E-ink Calendar
Requires=docker.service
After=docker.service
StartLimitBurst=3
StartLimitInterval=60s

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/docker-compose up -d
ExecStop=/usr/bin/docker-compose down
TimeoutStartSec=0
Restart=on-failure
RestartSec=30s

[Install]
WantedBy=multi-user.target
EOF

    # Enable service
    sudo systemctl daemon-reload
    sudo systemctl enable pokemon-calendar.service

    echo "✅ Systemd service created"
}

# Main installation sequence
main() {
    echo "🚀 Starting installation..."

    # Check for Docker
    if ! command -v docker &> /dev/null; then
        install_docker
        echo "⚠️  Docker was just installed. Please log out and back in, then run this script again."
        exit 0
    fi

    # Pi-specific setup
    enable_spi

    # Setup application
    setup_application

    # Create systemd service
    create_systemd_service

    echo ""
    echo "🎉 Installation completed!"
    echo ""
    echo "📋 Next Steps:"
    echo "1. Reboot to apply all changes:"
    echo "   sudo reboot"
    echo ""
    echo "2. After reboot, start the service:"
    echo "   sudo systemctl start pokemon-calendar.service"
    echo ""
    echo "3. Check status:"
    echo "   $INSTALL_DIR/status.sh"
    echo ""
    echo "🔧 Management Commands:"

    echo "🌐 Web Interface: http://$(hostname -I | awk '{print $1}'):8000"
    echo ""
    echo "📱 Auto-updates enabled via Watchtower"
    echo "   New Docker images will be pulled automatically"
    echo "   Updates check every hour"
}

# Run main installation
main