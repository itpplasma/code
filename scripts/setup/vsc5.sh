#!/usr/bin/env bash
# VSC5 cluster setup script
# Sets up .bashrc with correct spack modules and CODE activation

set -e

CODE_DIR="${CODE:-$HOME/code}"
BASHRC="$HOME/.bashrc"

# Marker to identify our managed block
MARKER_START="# === CODE/VSC5 Scientific Computing Environment ==="
MARKER_END="# === END CODE/VSC5 ==="

# The module configuration block
read -r -d '' MODULE_BLOCK << 'EOF' || true
# === CODE/VSC5 Scientific Computing Environment ===
# GCC 12.2.0 toolchain with compatible spack modules
# netcdf-fortran auto-loads: openmpi/4.1.5, hdf5/1.12.2 (with Fortran!), netcdf-c/4.9.0
module purge &>/dev/null
module load gcc/12.2.0-gcc-9.5.0-ohbahza &>/dev/null
module load mkl/2022.0.1 &>/dev/null
module load netcdf-fortran/4.6.0-gcc-12.2.0-ad7kryt &>/dev/null
module load fftw/3.3.10-gcc-12.2.0-jstgwxm &>/dev/null
module load python/3.12.8-gcc-12.2.0-4y5tbpr &>/dev/null
module load cmake/3.31.6-gcc-12.2.0-b55yivf &>/dev/null
module load boost/1.80.0-gcc-12.2.0-3fphho7 &>/dev/null

# Activate CODE environment (quietly)
source $HOME/code/activate.sh &>/dev/null
# === END CODE/VSC5 ===
EOF

# Check if we're on VSC5
if [[ ! "$(hostname)" == *vsc* ]] && [[ ! "$(hostname)" == l5* ]]; then
    echo "Warning: This script is intended for VSC5 cluster (hostname doesn't match)"
    read -p "Continue anyway? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Backup .bashrc
if [ -f "$BASHRC" ]; then
    cp "$BASHRC" "$BASHRC.backup.$(date +%Y%m%d_%H%M%S)"
    echo "Backed up $BASHRC"
fi

# Remove old block if it exists
if grep -q "$MARKER_START" "$BASHRC" 2>/dev/null; then
    echo "Removing existing CODE/VSC5 configuration..."
    sed -i "/$MARKER_START/,/$MARKER_END/d" "$BASHRC"
fi

# Also remove any old-style module loads and activate.sh that might be outside our block
sed -i '/^module purge/d' "$BASHRC"
sed -i '/^module load gcc\/12/d' "$BASHRC"
sed -i '/^module load mkl/d' "$BASHRC"
sed -i '/^module load netcdf-fortran/d' "$BASHRC"
sed -i '/^module load fftw/d' "$BASHRC"
sed -i '/^module load python/d' "$BASHRC"
sed -i '/^module load cmake/d' "$BASHRC"
sed -i '/source.*code\/activate.sh/d' "$BASHRC"
sed -i '/# Scientific computing modules/d' "$BASHRC"
sed -i '/# netcdf-fortran auto-loads/d' "$BASHRC"
sed -i '/# Activate code environment/d' "$BASHRC"

# Remove trailing blank lines
sed -i -e :a -e '/^\n*$/{$d;N;ba' -e '}' "$BASHRC"

# Append our block
echo "" >> "$BASHRC"
echo "$MODULE_BLOCK" >> "$BASHRC"

echo "VSC5 setup complete!"
echo "Module configuration added to $BASHRC"
echo ""
echo "To apply changes, run: source ~/.bashrc"
echo "Or start a new shell session."
