#!/bin/bash
set -e

# Health check endpoint for the web server
if [ "$1" = "health" ]; then
    exec curl -f http://localhost:${POKEMON_WEB_PORT:-8000}/health
fi

# Default behavior: run the passed command
exec "$@"