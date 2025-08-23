#!/bin/bash
# build_fast_dither.sh
# Build the Cython fast dithering module

echo "Building Cython fast dithering module..."

# Install required packages
echo "Installing Cython if needed..."
#sudo apt install -y python3-cython

# Clean previous builds
echo "Cleaning previous builds..."
rm -rf build/
rm -f fast_dither.c
rm -f fast_dither*.so

# Build the extension
echo "Building Cython extension..."
python3 setup.py build_ext --inplace

if [ $? -eq 0 ]; then
    echo "✅ Cython module built successfully!"
    echo "Testing import..."
    python3 -c "import fast_dither; print('Import successful!')"
    if [ $? -eq 0 ]; then
        echo "✅ Module ready to use!"
    else
        echo "❌ Module built but import failed"
        exit 1
    fi
else
    echo "❌ Build failed!"
    exit 1
fi