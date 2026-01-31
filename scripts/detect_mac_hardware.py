#!/usr/bin/env python3
"""
Mac Studio Hardware Detection for Optimal PDF Processing

Detects:
- CPU cores (physical and logical)
- Available RAM
- Disk I/O speed
- Recommends optimal worker count for parallel processing

Usage:
    python scripts/detect_mac_hardware.py
"""

import os
import sys
import platform
import subprocess
import json
from pathlib import Path

def get_cpu_info():
    """Get CPU core count using sysctl (Mac-specific)."""
    try:
        physical_cores = int(subprocess.check_output(
            ["sysctl", "-n", "hw.physicalcpu"],
            shell=False  # Explicit: prevent shell injection
        ).decode().strip())

        logical_cores = int(subprocess.check_output(
            ["sysctl", "-n", "hw.logicalcpu"],
            shell=False
        ).decode().strip())

        cpu_brand = subprocess.check_output(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            shell=False
        ).decode().strip()

        return {
            "physical_cores": physical_cores,
            "logical_cores": logical_cores,
            "cpu_brand": cpu_brand
        }
    except subprocess.CalledProcessError as e:
        print(f"Warning: sysctl command failed: {e}")
        return {
            "physical_cores": 4,  # Conservative fallback
            "logical_cores": 8,
            "cpu_brand": "Unknown"
        }
    except (ValueError, UnicodeDecodeError) as e:
        print(f"Warning: Could not parse CPU info: {e}")
        return {
            "physical_cores": 4,
            "logical_cores": 8,
            "cpu_brand": "Unknown"
        }
    except Exception as e:
        print(f"Warning: Unexpected error detecting CPU: {e}")
        return {
            "physical_cores": 4,
            "logical_cores": 8,
            "cpu_brand": "Unknown"
        }

def get_memory_info():
    """Get total RAM in GB."""
    try:
        mem_bytes = int(subprocess.check_output(
            ["sysctl", "-n", "hw.memsize"],
            shell=False  # Explicit: prevent shell injection
        ).decode().strip())

        mem_gb = mem_bytes / (1024**3)
        return round(mem_gb, 2)
    except subprocess.CalledProcessError as e:
        print(f"Warning: sysctl command failed: {e}")
        return 16.0  # Conservative fallback
    except (ValueError, UnicodeDecodeError) as e:
        print(f"Warning: Could not parse memory info: {e}")
        return 16.0
    except Exception as e:
        print(f"Warning: Unexpected error detecting memory: {e}")
        return 16.0

def recommend_workers(physical_cores, memory_gb):
    """
    Recommend optimal worker count for PDF processing.

    Strategy:
    - Use ProcessPoolExecutor (not ThreadPoolExecutor) to bypass GIL
    - Reserve 2 cores for OS/system (not all cores)
    - Ensure at least 2GB RAM per worker for Docling
    - Cap at 12 workers to avoid Azure throttling

    Returns:
        Tuple of (recommended_workers, explanation_dict)
    """
    # Strategy 1: Core-based limit
    core_based = max(1, physical_cores - 2)  # Leave 2 cores for system

    # Strategy 2: Memory-based limit (2GB per worker for Docling)
    memory_based = int(memory_gb / 2.0)

    # Take minimum of both constraints, cap at 12
    recommended = min(core_based, memory_based, 12)
    recommended = max(recommended, 1)  # At least 1 worker

    # Determine limiting factor
    if recommended == core_based and recommended < memory_based and recommended < 12:
        limiting_factor = "CPU cores"
        bottleneck = f"Limited by available cores ({physical_cores} total - 2 reserved)"
    elif recommended == memory_based and recommended < core_based and recommended < 12:
        limiting_factor = "RAM"
        bottleneck = f"Limited by memory ({memory_gb} GB / 2 GB per worker)"
    elif recommended == 12:
        limiting_factor = "Azure throttling cap"
        bottleneck = "Capped at 12 to prevent Azure API throttling"
    else:
        limiting_factor = "balanced"
        bottleneck = f"Optimal balance of cores and memory"

    explanation = {
        "core_based_max": core_based,
        "memory_based_max": memory_based,
        "azure_cap": 12,
        "recommended": recommended,
        "limiting_factor": limiting_factor,
        "bottleneck": bottleneck
    }

    return recommended, explanation

def print_recommendations():
    """Print hardware info and recommendations."""
    cpu = get_cpu_info()
    memory = get_memory_info()
    workers, explanation = recommend_workers(cpu["physical_cores"], memory)

    print("\n" + "="*70)
    print("HARDWARE DETECTION - ADAPTIVE CONFIGURATION")
    print("="*70)
    print(f"\nCPU: {cpu['cpu_brand']}")
    print(f"  Physical Cores: {cpu['physical_cores']}")
    print(f"  Logical Cores:  {cpu['logical_cores']}")
    print(f"\nMemory: {memory} GB")

    print(f"\n{'='*70}")
    print("WORKER CALCULATION (ADAPTIVE)")
    print("="*70)
    print(f"\n  CPU-based max:    {explanation['core_based_max']} workers ({cpu['physical_cores']} cores - 2 reserved)")
    print(f"  Memory-based max: {explanation['memory_based_max']} workers ({memory} GB / 2 GB per worker)")
    print(f"  Azure API cap:    {explanation['azure_cap']} workers (prevent throttling)")
    print(f"\n  Limiting factor:  {explanation['limiting_factor']}")
    print(f"  {explanation['bottleneck']}")

    print(f"\n{'='*70}")
    print("RECOMMENDED CONFIGURATION")
    print("="*70)
    print(f"\n✅ Optimal Worker Count: {workers}")

    # Calculate expected performance
    pdf_per_sec = workers * 0.12  # Rough estimate: 0.12 PDF/sec per worker
    time_for_50 = 50 / pdf_per_sec if pdf_per_sec > 0 else 0
    print(f"\n   Expected Performance:")
    print(f"   - Throughput: ~{pdf_per_sec:.1f} PDFs/second")
    print(f"   - Time for 50 PDFs: ~{time_for_50:.0f} seconds")

    print(f"\n   Usage:")
    print(f"   python scripts/optimized_batch_ingest.py --workers {workers}")

    # Save to config file for scripts to read
    config = {
        "cpu_physical_cores": cpu["physical_cores"],
        "cpu_logical_cores": cpu["logical_cores"],
        "cpu_brand": cpu["cpu_brand"],
        "memory_gb": memory,
        "recommended_workers": workers,
        "calculation_details": explanation,
        "timestamp": subprocess.check_output(["date"], shell=False).decode().strip()
    }

    config_path = Path(__file__).parent.parent / "hardware_config.json"
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)

    print(f"\n💾 Hardware config saved to: {config_path}")
    print("="*70 + "\n")

    return config

if __name__ == "__main__":
    config = print_recommendations()
