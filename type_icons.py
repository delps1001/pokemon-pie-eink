#!/usr/bin/env python3
"""
Type Icon Helper - Maps Pokemon types to their corresponding icon files
Handles generation-aware type icon selection for the Pokemon E-ink Calendar
"""

from pathlib import Path

# Type name to ID mapping (based on PokeAPI and sprite structure)
TYPE_NAME_TO_ID = {
    "normal": 1,
    "fighting": 2, 
    "flying": 3,
    "poison": 4,
    "ground": 5,
    "rock": 6,
    "bug": 7,
    "ghost": 8,
    "steel": 9,
    "fire": 10,
    "water": 11,
    "grass": 12,
    "electric": 13,
    "psychic": 14,
    "ice": 15,
    "dragon": 16,
    "dark": 17,
    "fairy": 18,
    # Special shadow type in some generations
    "shadow": 10001
}

# Generation to sprite directory mapping
GENERATION_TO_SPRITE_DIR = {
    1: "generation-iii/firered-leafgreen",  # Use FR/LG for Gen 1 (cleaner than originals)
    2: "generation-iii/emerald",            # Emerald for Gen 2
    3: "generation-iii/emerald",            # Emerald for Gen 3
    4: "generation-iv/platinum",            # Platinum for Gen 4
    5: "generation-v/black-white",          # B/W for Gen 5
    6: "generation-vi/x-y",                 # X/Y for Gen 6
    7: "generation-vii/sun-moon",           # Sun/Moon for Gen 7
    8: "generation-viii/sword-shield",      # Sword/Shield for Gen 8
    9: "generation-ix/scarlet-violet",      # Scarlet/Violet for Gen 9
}

def get_type_icon_path(type_name, generation, sprites_base_dir="sprites/sprites/sprites/types"):
    """
    Get the file path for a Pokemon type icon based on type and generation
    
    Args:
        type_name (str): Pokemon type name (e.g., "fire", "water")
        generation (int): Pokemon generation (1-9)
        sprites_base_dir (str): Base directory for type sprites
    
    Returns:
        Path: Path to the type icon file, or None if not found
    """
    type_id = TYPE_NAME_TO_ID.get(type_name.lower())
    if not type_id:
        return None
    
    sprite_subdir = GENERATION_TO_SPRITE_DIR.get(generation, "generation-iii/emerald")
    
    # Construct the full path
    icon_path = Path(sprites_base_dir) / sprite_subdir / f"{type_id}.png"
    
    # Check if file exists, fallback to Generation III if not
    if not icon_path.exists():
        fallback_path = Path(sprites_base_dir) / "generation-iii/emerald" / f"{type_id}.png"
        if fallback_path.exists():
            return fallback_path
    
    return icon_path if icon_path.exists() else None

def get_all_type_icons_for_pokemon(types, generation, sprites_base_dir="sprites/sprites/sprites/types"):
    """
    Get all type icon paths for a Pokemon
    
    Args:
        types (list): List of type names for the Pokemon
        generation (int): Pokemon generation
        sprites_base_dir (str): Base directory for type sprites
    
    Returns:
        list: List of Path objects for type icons
    """
    icon_paths = []
    for type_name in types:
        icon_path = get_type_icon_path(type_name, generation, sprites_base_dir)
        if icon_path:
            icon_paths.append(icon_path)
    
    return icon_paths

def list_available_generations():
    """List all available generations with type icons"""
    return list(GENERATION_TO_SPRITE_DIR.keys())

def list_available_types():
    """List all available Pokemon types"""
    return list(TYPE_NAME_TO_ID.keys())

if __name__ == "__main__":
    # Test the type icon system
    print("🎨 Pokemon Type Icon System Test")
    print("=" * 40)
    
    # Test some common Pokemon
    test_cases = [
        ("fire", 1, "Charmander (Gen 1)"),
        ("water", 1, "Squirtle (Gen 1)"),
        ("electric", 1, "Pikachu (Gen 1)"),
        ("fairy", 6, "Sylveon (Gen 6)"),
        ("steel", 4, "Dialga (Gen 4)")
    ]
    
    for type_name, generation, description in test_cases:
        icon_path = get_type_icon_path(type_name, generation)
        status = "✅ Found" if icon_path and icon_path.exists() else "❌ Missing"
        print(f"{status} {description}: {type_name.title()} type")
        if icon_path:
            print(f"      Path: {icon_path}")
        print()
    
    print("Available generations:", list_available_generations())
    print("Available types:", len(list_available_types()), "types")