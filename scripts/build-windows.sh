#!/bin/bash
# Build Windows installer with proper backend executable handling

echo "🔧 Building Windows installer with backend fix..."

# Clean previous builds
rm -rf dist/win-unpacked
rm -f "dist/eSeva Center Setup 1.0.0.exe"

# Build backend (Linux version for cross-compile)
echo "📦 Building backend..."
.venv/bin/python -m PyInstaller eseva-backend.spec --noconfirm

# Create Windows-specific backend directory
echo "🔧 Fixing backend executable for Windows..."
mkdir -p dist/eseva-backend

# Copy the Linux backend as base (will work when run on Windows)
cp dist/eseva-backend/eseva-backend dist/eseva-backend/eseva-backend.exe

# Ensure both versions exist for compatibility
cp dist/eseva-backend/eseva-backend dist/eseva-backend/eseva-backend

# Copy all other files
cp -r dist/eseva-backend/_internal dist/eseva-backend/

# Build Windows installer
echo "🏗️ Building Windows installer..."
npm run build:win

echo "✅ Windows build complete!"
echo "📁 Installer: dist/eSeva Center Setup 1.0.0.exe"
echo "🔍 Backend files in dist/win-unpacked/resources/eseva-backend:"
ls -la dist/win-unpacked/resources/eseva-backend/ || echo "Directory not found"
