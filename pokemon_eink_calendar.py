#!/usr/bin/env python3
# -*- coding:utf-8 -*-
"""
Pokemon E-ink Calendar for Raspberry Pi
Displays a different Pokemon each day on a Waveshare 7.5" HD e-ink display
"""

import sys
import os
import time
import json
import logging
from datetime import datetime, timedelta
from PIL import Image, ImageChops, ImageDraw, ImageEnhance
import argparse
from pathlib import Path
import schedule
import threading
import numpy as np
import random
import hashlib

# Import Pokemon data with types and generations
from pokemon_data_with_types import POKEMON_DATA, get_pokemon_info, get_pokemon_types, get_pokemon_generation
from pokemon_pokedex_descriptions import get_pokedex_description

# Add the waveshare library path - support multiple display types
try:
    from waveshare_epd import epd7in5_HD, epd7in3e, epd7in5_V2
except ImportError:
    print("Warning: waveshare_epd library not found. Running in simulation mode.")
    epd7in5_HD = None
    epd7in3e = None
    epd7in5_V2 = None

# Import color mapping for 7-color display
from color_mapping import SevenColorMapper
from picolay import Box, Img, Style, Text, compute_layout
from picolay import render as picolay_render
from picolay.text import font_for, line_pitch, measuring, text_width

# Where to look for fonts, and the (regular, bold) pairs to look for, in order of preference
FONT_DIRS = [
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/truetype/liberation",
    "/usr/share/fonts/TTF",
    "/Library/Fonts",
    os.path.expanduser("~/Library/Fonts"),
]
FONT_FAMILIES = [
    ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
    ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf"),
]

# The colour panel's own inks. Flat areas in these show solid; anything else is dithered.
BLACK, WHITE = (0, 0, 0), (255, 255, 255)
RED, YELLOW, BLUE, GREEN = (255, 0, 0), (255, 255, 0), (0, 0, 255), (0, 255, 0)

# Type chip colours as (fill, ink). A white fill is drawn as an outline in the ink colour.
TYPE_CHIPS = {
    "normal": (WHITE, BLACK), "fire": (RED, WHITE), "water": (BLUE, WHITE),
    "grass": (GREEN, WHITE), "electric": (YELLOW, BLACK), "ice": (WHITE, BLUE),
    "fighting": (RED, WHITE), "poison": (BLUE, WHITE), "ground": (YELLOW, BLACK),
    "flying": (WHITE, BLUE), "psychic": (WHITE, RED), "bug": (WHITE, GREEN),
    "rock": (YELLOW, BLACK), "ghost": (BLUE, WHITE), "dragon": (BLUE, WHITE),
    "dark": (BLACK, WHITE), "steel": (WHITE, BLACK), "fairy": (WHITE, RED),
}

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()  # Only console output for Docker
    ]
)

class PokemonEInkCalendar:
    def __init__(self, demo_mode=False, cache_dir=None, config_file="./config.json", enable_web_server=False, web_host="0.0.0.0", web_port=8000):
        self.demo_mode = demo_mode
        self.config_file = config_file
        self.enable_web_server = enable_web_server
        self.web_host = web_host
        self.web_port = web_port
        self.web_server = None
        
        # Load configuration
        self.config = self.load_config()
        
        # Override demo mode from config if not explicitly set
        if not demo_mode and self.config.get('demo', {}).get('enabled', False):
            self.demo_mode = True
        
        # Set cache directory from config or parameter
        self.cache_dir = Path(cache_dir or self.config.get('cache', {}).get('directory', './pokemon_cache'))
        self.cache_dir.mkdir(exist_ok=True)
        
        # E-ink display configuration
        display_config = self.config.get('display', {})
        self.display_type = display_config.get('type', '7in5_HD')  # Default to current display
        self.display_width = display_config.get('width', 880)
        self.display_height = display_config.get('height', 528)
        self.color_mode = display_config.get('color_mode', 'monochrome')  # 'monochrome' or '7color'
        
        # Auto-configure dimensions based on display type
        if self.display_type == '7in3e':
            self.display_width = 800
            self.display_height = 480
            self.color_mode = '7color'
        elif self.display_type == '7in5_HD':
            self.display_width = display_config.get('width', 880)
            self.display_height = display_config.get('height', 528)
            self.color_mode = 'monochrome'
        elif self.display_type == '7in5_V2':
            self.display_width = 800
            self.display_height = 480
            self.color_mode = 'monochrome'
        
        # Initialize color mapper for 7-color displays
        self.color_mapper = None
        if self.color_mode == '7color':
            self.color_mapper = SevenColorMapper()
            logging.info("Initialized 7-color mapper for vibrant display mode")
        
        # Pokemon configuration
        pokemon_config = self.config.get('pokemon', {})
        self.start_pokemon_id = pokemon_config.get('start_pokemon_id', 1)
        self.start_date = datetime.strptime(pokemon_config.get('start_date', '2024-01-01'), '%Y-%m-%d')
        self.cycle_all_pokemon = pokemon_config.get('cycle_all_pokemon', True)
        self.custom_pokemon_list = pokemon_config.get('custom_pokemon_list', [])

        # Shiny Pokemon (color displays only): each day, each Pokemon has a
        # 1-in-shiny_odds chance of showing its shiny sprite
        self.shiny_enabled = pokemon_config.get('shiny_enabled', True)
        self.shiny_odds = max(1, int(pokemon_config.get('shiny_odds', 100)))
        
        # Pokemon data
        self.pokemon_data = []
        self.current_pokemon_index = 0
        
        # E-Paper safety tracking (following manufacturer precautions)
        # Note: Waveshare library only does full refreshes, no partial refresh support
        safety_config = display_config.get('epaper_safety', {})
        self.last_refresh_time = None
        self.min_refresh_interval = safety_config.get('min_refresh_interval_seconds', 180)  # Minimum 180 seconds between refreshes
        self.last_full_refresh = None
        self.max_hours_without_refresh = safety_config.get('max_hours_without_refresh', 24)  # Force refresh at least every 24 hours
        self.border_register = safety_config.get('border_register', '0x3C')  # Default border register
        
        # Initialize display based on type
        self.epd = None
        self.epd_type = None
        
        if self.display_type == '7in5_HD' and epd7in5_HD:
            try:
                logging.info("Starting 7.5\" HD display initialization...")
                self.epd = epd7in5_HD.EPD()
                logging.info("EPD object created, calling init()...")
                
                # Add timeout mechanism for init()
                import signal
                def timeout_handler(signum, frame):
                    raise TimeoutError("EPD init() timed out after 30 seconds")
                
                signal.signal(signal.SIGALRM, timeout_handler)
                signal.alarm(30)  # 30 second timeout
                
                try:
                    self.epd.init()
                    signal.alarm(0)  # Cancel timeout
                    logging.info("EPD init() completed, calling Clear()...")
                    self.epd.Clear()
                    logging.info("EPD Clear() completed")
                    self.epd_type = '7in5_HD'
                    logging.info("7.5\" HD E-ink display initialized successfully")
                except TimeoutError as te:
                    signal.alarm(0)  # Cancel timeout
                    raise te
                    
            except Exception as e:
                logging.error(f"Failed to initialize 7.5\" HD display: {e}")
                logging.error(f"Exception type: {type(e).__name__}")
                logging.error(f"Exception details: {str(e)}")
                self.epd = None
                # Fall back to simulation mode
                logging.info("Falling back to simulation mode due to display initialization failure")
        
        elif self.display_type == '7in3e' and epd7in3e:
            try:
                logging.info("Starting 7.3\" 7-color display initialization...")
                self.epd = epd7in3e.EPD()
                logging.info("EPD object created, calling init()...")
                
                # Add timeout mechanism for init()
                import signal
                def timeout_handler(signum, frame):
                    raise TimeoutError("EPD init() timed out after 30 seconds")
                
                signal.signal(signal.SIGALRM, timeout_handler)
                signal.alarm(30)  # 30 second timeout
                
                try:
                    self.epd.init()
                    signal.alarm(0)  # Cancel timeout
                    logging.info("EPD init() completed, calling Clear()...")
                    self.epd.Clear()
                    logging.info("EPD Clear() completed")
                    self.epd_type = '7in3e'
                    logging.info("7.3\" 7-color E-ink display initialized successfully")
                except TimeoutError as te:
                    signal.alarm(0)  # Cancel timeout
                    raise te
                    
            except Exception as e:
                logging.error(f"Failed to initialize 7.3\" 7-color display: {e}")
                logging.error(f"Exception type: {type(e).__name__}")
                logging.error(f"Exception details: {str(e)}")
                self.epd = None
                # Fall back to simulation mode
                logging.info("Falling back to simulation mode due to display initialization failure")
        elif self.display_type == '7in5_V2' and epd7in5_V2:
            try:
                logging.info("Starting 7.5\" V2 display initialization...")
                self.epd = epd7in5_V2.EPD()
                logging.info("EPD object created, calling init()...")
                
                # Add timeout mechanism for init()
                import signal
                def timeout_handler(signum, frame):
                    raise TimeoutError("EPD init() timed out after 30 seconds")
                
                signal.signal(signal.SIGALRM, timeout_handler)
                signal.alarm(30)  # 30 second timeout
                
                try:
                    self.epd.init()
                    signal.alarm(0)  # Cancel timeout
                    logging.info("EPD init() completed, calling Clear()...")
                    self.epd.Clear()
                    logging.info("EPD Clear() completed")
                    self.epd_type = '7in5_V2'
                    logging.info("7.5\" V2 E-ink display initialized successfully")
                except TimeoutError as te:
                    signal.alarm(0)  # Cancel timeout
                    raise te
                    
            except Exception as e:
                logging.error(f"Failed to initialize 7.5\" V2 display: {e}")
                logging.error(f"Exception type: {type(e).__name__}")
                logging.error(f"Exception details: {str(e)}")
                self.epd = None
                # Fall back to simulation mode
                logging.info("Falling back to simulation mode due to display initialization failure")
        
        else:
            logging.info(f"Running in simulation mode - {self.display_type} display not available")
        
        # Find the regular and bold fonts for the layout
        self.load_fonts()
        
        # Load Pokemon data (sprites assumed to be pre-cached)
        self.load_pokemon_data()
        
        # Initialize web server if enabled
        if self.enable_web_server:
            try:
                from web_server import PokemonWebServer
                self.web_server = PokemonWebServer(
                    pokemon_calendar=self,
                    host=self.web_host,
                    port=self.web_port
                )
                logging.info(f"Web server initialized on {self.web_host}:{self.web_port}")
            except ImportError:
                logging.error("Failed to import web_server module. Web server disabled.")
                self.web_server = None
            except Exception as e:
                logging.error(f"Failed to initialize web server: {e}")
                self.web_server = None
        
        logging.info(f"Pokemon E-ink Calendar initialized")
        logging.info(f"Starting from Pokemon #{self.start_pokemon_id} on {self.start_date.strftime('%Y-%m-%d')}")
        logging.info(f"Demo mode: {self.demo_mode}")
        if self.enable_web_server and self.web_server:
            logging.info(f"Web control available at http://{self.web_host}:{self.web_port}")
        
        # Log e-Paper safety configuration
        logging.info(f"E-Paper safety enabled: min refresh interval {self.min_refresh_interval}s, max refresh interval {self.max_hours_without_refresh}h")

    def can_refresh_display(self):
        """Check if display can be refreshed based on e-Paper safety rules"""
        current_time = time.time()
        
        # Check minimum refresh interval (180 seconds)
        if self.last_refresh_time:
            time_since_last = current_time - self.last_refresh_time
            if time_since_last < self.min_refresh_interval:
                remaining = self.min_refresh_interval - time_since_last
                logging.info(f"E-Paper safety: Refresh blocked, {remaining:.1f}s remaining until next allowed refresh")
                return False
        
        return True
    
    def needs_full_refresh(self):
        """Check if a full refresh is required based on e-Paper safety rules"""
        current_time = time.time()
        
        # Force refresh if more than 24 hours since last refresh
        if self.last_full_refresh:
            hours_since_full = (current_time - self.last_full_refresh) / 3600
            if hours_since_full >= self.max_hours_without_refresh:
                logging.info(f"E-Paper safety: Full refresh required, {hours_since_full:.1f} hours since last refresh")
                return True
        
        return False
    
    def set_border_color(self, border_mode=None):
        """Configure border color using manufacturer register controls (0x3C or 0x50)"""
        if not self.epd:
            return
        
        if border_mode is None:
            # Use configured border register or default to 0x3C
            border_mode = int(self.border_register, 16) if isinstance(self.border_register, str) else self.border_register
        
        try:
            # Only attempt border control for supported displays
            if hasattr(self.epd, 'send_command') and hasattr(self.epd, 'send_data'):
                logging.info(f"Setting e-Paper border control register: 0x{border_mode:02X}")
                # Note: Actual register implementation depends on Waveshare library
                # This is a placeholder for the proper border control
            else:
                logging.info("Border color control not available for this display type")
        except Exception as e:
            logging.warning(f"Failed to set border color: {e}")
    
    def prepare_for_storage(self):
        """Clear screen multiple times before long-term storage (manufacturer recommendation)"""
        if not self.epd:
            logging.warning("Cannot prepare for storage - no display available")
            return
        
        try:
            logging.info("Preparing e-Paper display for storage (clearing screen multiple times)")
            
            # Clear screen 3 times as recommended by manufacturer
            for i in range(3):
                logging.info(f"Storage preparation: Clear {i+1}/3")
                self.epd.Clear()
                if i < 2:  # Don't sleep after the last clear
                    time.sleep(2)  # Brief pause between clears
            
            # Put display to sleep
            self.epd.sleep()
            logging.info("E-Paper display prepared for storage and put to sleep")
            
        except Exception as e:
            logging.error(f"Failed to prepare display for storage: {e}")

    def load_config(self):
        """Load configuration from JSON file"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                logging.info(f"Configuration loaded from {self.config_file}")
                return config
            else:
                logging.warning(f"Config file {self.config_file} not found, using defaults")
                return {}
        except Exception as e:
            logging.error(f"Failed to load config: {e}")
            return {}

    def load_fonts(self):
        """Find a regular and a bold face of the same family for the layout"""
        self.font_regular = None  # None: picolay falls back to its bundled DejaVu Sans
        self.font_bold = None
        for regular_name, bold_name in FONT_FAMILIES:
            for font_dir in FONT_DIRS:
                regular_path = os.path.join(font_dir, regular_name)
                bold_path = os.path.join(font_dir, bold_name)
                if os.path.exists(regular_path) and os.path.exists(bold_path):
                    self.font_regular, self.font_bold = regular_path, bold_path
                    logging.info(f"Loaded fonts {regular_name} and {bold_name} from {font_dir}")
                    return
        logging.warning("No regular/bold font pair found; using picolay's bundled font for all text")

    def load_pokemon_data(self):
        """Load Pokemon data and assume sprites are already available locally"""
        # Check if we have a sprites directory (either earliest_pokemon_sprites or pokemon_cache)
        sprite_dirs = [
            Path("earliest_pokemon_sprites"),  # Preferred: crispy earliest sprites
            self.cache_dir  # Fallback: original cache directory
        ]
        
        self.sprite_dir = None
        for sprite_dir in sprite_dirs:
            if sprite_dir.exists() and any(sprite_dir.glob("*.png")):
                self.sprite_dir = sprite_dir
                logging.info(f"Using sprites from: {sprite_dir}")
                break
        
        if not self.sprite_dir:
            logging.error("No sprite directory found! Please run extract_earliest_sprites.py first or ensure pokemon_cache exists")
            raise FileNotFoundError("No Pokemon sprites found. Run extract_earliest_sprites.py to generate sprites.")
        
        # Generate Pokemon data for IDs 1-1025 using comprehensive database
        self.pokemon_data = []
        for pokemon_id in range(1, 1026):
            # Find sprite file for this Pokemon (try zero-padded format first)
            sprite_files = list(self.sprite_dir.glob(f"{pokemon_id:04d}.png"))
            if not sprite_files:
                # Try 3-digit format as fallback
                sprite_files = list(self.sprite_dir.glob(f"{pokemon_id:03d}_*.png"))
            
            if sprite_files:
                sprite_path = sprite_files[0]
                # Get comprehensive Pokemon data from new database
                pokemon_info = get_pokemon_info(pokemon_id)
                if pokemon_info:
                    pokemon_data = {
                        'id': pokemon_id,
                        'name': pokemon_info['name'],
                        'types': pokemon_info['types'],
                        'generation': pokemon_info['generation'],
                        'local_sprite': str(sprite_path)
                    }
                else:
                    # Fallback for missing Pokemon
                    pokemon_data = {
                        'id': pokemon_id,
                        'name': f"Pokemon #{pokemon_id:03d}",
                        'types': ["normal"],
                        'generation': 1,
                        'local_sprite': str(sprite_path)
                    }
                
                self.pokemon_data.append(pokemon_data)
        
        logging.info(f"Loaded {len(self.pokemon_data)} Pokemon with proper names")
        
        if len(self.pokemon_data) == 0:
            logging.error("No Pokemon sprites found in any directory!")
            raise FileNotFoundError("No Pokemon sprites found")

        self.load_shiny_sprites()

    def load_shiny_sprites(self):
        """Attach shiny sprite paths (earliest_pokemon_sprites_shiny/) to the Pokemon data"""
        shiny_dir = Path("earliest_pokemon_sprites_shiny")
        if not shiny_dir.exists():
            logging.info("No shiny sprite directory found; shiny Pokemon disabled "
                         "(run extract_earliest_sprites.py to generate them)")
            return

        count = 0
        for pokemon in self.pokemon_data:
            shiny_path = shiny_dir / f"{pokemon['id']:04d}.png"
            if shiny_path.exists():
                pokemon['local_shiny_sprite'] = str(shiny_path)
                count += 1
        logging.info(f"Loaded {count} shiny sprites from: {shiny_dir}")

    def is_shiny(self, pokemon, date=None):
        """Whether this Pokemon is shiny on the given date (default: today).

        Only applies to color displays. The roll is a 1-in-shiny_odds chance made
        deterministically from the date and Pokemon ID, so every refresh, preview and
        restart on the same day agrees, and a given Pokemon can be shiny on some days
        and not others. (hashlib rather than hash(), which is randomized per process.)
        """
        if not self.shiny_enabled or self.color_mode != '7color':
            return False
        if not pokemon.get('local_shiny_sprite'):
            return False
        date = date or datetime.now().date()
        digest = hashlib.sha256(f"shiny:{date.isoformat()}:{pokemon['id']}".encode()).digest()
        return int.from_bytes(digest[:8], 'big') % self.shiny_odds == 0

    def enhance_sprite_for_eink(self, sprite):
        """
        Smart e-ink processing - adapts to display type
        Monochrome: Uses Floyd-Steinberg dithering and gamma correction
        7-Color: Uses perceptual color quantization for vibrant display
        """
        try:
            # Handle transparency properly for both display types
            if sprite.mode in ('RGBA', 'LA') or 'transparency' in sprite.info:
                background = Image.new('RGB', sprite.size, (255, 255, 255))
                if sprite.mode == 'RGBA':
                    background.paste(sprite, mask=sprite.split()[-1])
                else:
                    background.paste(sprite, (0, 0))
                sprite = background
            
            # Different processing based on display type
            if self.color_mode == '7color' and self.color_mapper:
                # 7-Color display processing
                import time
                start_time = time.time()
                sprite_size = sprite.size
                logging.info(f"Processing sprite for 7-color vibrant display (size: {sprite_size[0]}x{sprite_size[1]})")
                
                # Convert to RGB if needed
                if sprite.mode != 'RGB':
                    sprite = sprite.convert('RGB')
                
                # Enhance for vibrant colors
                logging.info("Enhancing sprite colors for vibrant display...")
                sprite = self.color_mapper.enhance_for_vibrant_display(sprite)
                enhance_time = time.time()
                logging.info(f"Color enhancement completed in {enhance_time - start_time:.2f}s")
                
                # Get dithering method from config
                image_config = self.config.get('image_processing', {})
                dithering_method = image_config.get('dithering_algorithm', 'floyd_steinberg_7color')
                
                # Apply advanced 7-color dithering using state-of-the-art error diffusion
                logging.info(f"Applying advanced 7-color dithering ({dithering_method})...")
                dither_start = time.time()
                result = self.color_mapper.quantize_image_advanced_dithering(
                    sprite, method=dithering_method
                )
                dither_time = time.time()
                
                total_time = dither_time - start_time
                dither_only_time = dither_time - dither_start
                pixels_processed = sprite_size[0] * sprite_size[1]
                pixels_per_second = pixels_processed / dither_only_time if dither_only_time > 0 else 0
                
                logging.info(f"Advanced 7-color dithering completed: {dithering_method}")
                logging.info(f"Dithering performance: {dither_only_time:.2f}s ({pixels_per_second:,.0f} pixels/sec)")
                logging.info(f"Total sprite processing time: {total_time:.2f}s")
                return result
            
            else:
                # Monochrome display processing (original method)
                logging.info("Processing sprite for monochrome display")
                
                # Convert to grayscale
                if sprite.mode != 'L':
                    sprite = sprite.convert('L')
                
                # Apply gamma correction (Kindle standard)
                gamma = 2.2
                sprite = sprite.point(lambda x: int(255 * ((x / 255) ** (1/gamma))))
                
                # Slight contrast enhancement (conservative, like e-readers)
                enhancer = ImageEnhance.Contrast(sprite)
                sprite = enhancer.enhance(1.15)
                
                # Apply Floyd-Steinberg dithering (industry standard)
                result = self.floyd_steinberg_dither(sprite)
                
                logging.info("Applied Floyd-Steinberg dithering for monochrome display")
                return result
            
        except Exception as e:
            logging.warning(f"Sprite enhancement failed, using fallback: {e}")
            return self.simple_threshold(sprite)

    def floyd_steinberg_dither(self, image):
        """
        Floyd-Steinberg dithering - the gold standard for e-ink displays
        Used by Kindle and other professional e-readers
        """
        try:
            import numpy as np
            
            # Convert to numpy array for processing
            img_array = np.array(image, dtype=np.float32)
            height, width = img_array.shape
            
            # Apply Floyd-Steinberg error diffusion
            for y in range(height):
                for x in range(width):
                    old_pixel = img_array[y, x]
                    new_pixel = 255 if old_pixel > 127 else 0
                    img_array[y, x] = new_pixel
                    
                    # Calculate quantization error
                    error = old_pixel - new_pixel
                    
                    # Distribute error to neighboring pixels (Floyd-Steinberg pattern)
                    if x + 1 < width:
                        img_array[y, x + 1] += error * 7/16
                    if y + 1 < height:
                        if x > 0:
                            img_array[y + 1, x - 1] += error * 3/16
                        img_array[y + 1, x] += error * 5/16
                        if x + 1 < width:
                            img_array[y + 1, x + 1] += error * 1/16
            
            # Convert back to PIL image
            result_array = np.clip(img_array, 0, 255).astype(np.uint8)
            return Image.fromarray(result_array, 'L').convert('1')
            
        except Exception as e:
            logging.warning(f"Floyd-Steinberg dithering failed: {e}")
            # Fallback to simple threshold
            return self.simple_threshold(image)

    def simple_threshold(self, sprite):
        """
        Simple, reliable threshold method - proven backup
        """
        try:
            # Handle transparency
            if sprite.mode in ('RGBA', 'LA') or 'transparency' in sprite.info:
                background = Image.new('RGB', sprite.size, (255, 255, 255))
                if sprite.mode == 'RGBA':
                    background.paste(sprite, mask=sprite.split()[-1])
                else:
                    background.paste(sprite, (0, 0))
                sprite = background
            
            # Convert to grayscale
            if sprite.mode != 'L':
                sprite = sprite.convert('L')
            
            # Use Waveshare's recommended threshold for mixed content
            threshold = 160
            
            result = sprite.point(lambda x: 0 if x < threshold else 255, '1')
            logging.info(f"Using simple threshold: {threshold}")
            return result
            
        except Exception as e:
            logging.error(f"Even simple threshold failed: {e}")
            # Ultimate fallback
            if sprite.mode != 'L':
                sprite = sprite.convert('L')
            return sprite.point(lambda x: 0 if x < 128 else 255, '1')

    def get_current_pokemon(self):
        """Get the Pokemon for today based on configuration"""
        if self.demo_mode:
            # In demo mode, cycle through all Pokemon starting from configured Pokemon
            if self.custom_pokemon_list:
                # Use custom list if provided
                return self.custom_pokemon_list[self.current_pokemon_index % len(self.custom_pokemon_list)]
            else:
                # Start from configured Pokemon ID and cycle through all
                start_index = self.find_pokemon_index(self.start_pokemon_id)
                actual_index = (start_index + self.current_pokemon_index) % len(self.pokemon_data)
                return self.pokemon_data[actual_index]
        else:
            # Normal mode: calculate Pokemon based on days since start date
            today = datetime.now().date()
            start_date = self.start_date.date()
            
            # Calculate days since start date
            days_since_start = (today - start_date).days
            
            if days_since_start < 0:
                # If today is before start date, use the starting Pokemon
                days_since_start = 0
            
            if self.custom_pokemon_list:
                # Use custom Pokemon list
                pokemon_index = days_since_start % len(self.custom_pokemon_list)
                return self.custom_pokemon_list[pokemon_index]
            else:
                # Start from configured Pokemon ID and count up
                start_index = self.find_pokemon_index(self.start_pokemon_id)
                
                if self.cycle_all_pokemon:
                    # Cycle through all Pokemon starting from start_pokemon_id
                    actual_index = (start_index + days_since_start) % len(self.pokemon_data)
                    return self.pokemon_data[actual_index]
                else:
                    # Only count up from start Pokemon (don't cycle back)
                    actual_index = min(start_index + days_since_start, len(self.pokemon_data) - 1)
                    return self.pokemon_data[actual_index]

    def find_pokemon_index(self, pokemon_id):
        """Find the index of a Pokemon by its ID"""
        for i, pokemon in enumerate(self.pokemon_data):
            if pokemon['id'] == pokemon_id:
                return i
        
        # If not found, default to first Pokemon
        logging.warning(f"Pokemon ID {pokemon_id} not found, using first Pokemon")
        return 0

    def get_pokemon_by_id(self, pokemon_id):
        """Get Pokemon data by specific ID for preview functionality"""
        for pokemon in self.pokemon_data:
            if pokemon['id'] == pokemon_id:
                return pokemon
        
        # If not found, return None
        logging.warning(f"Pokemon ID {pokemon_id} not found in loaded data")
        return None

    def get_pokemon_info_for_date(self, target_date):
        """Get Pokemon info for a specific date (useful for planning)"""
        if isinstance(target_date, str):
            target_date = datetime.strptime(target_date, '%Y-%m-%d').date()
        
        start_date = self.start_date.date()
        days_since_start = (target_date - start_date).days
        
        if days_since_start < 0:
            days_since_start = 0
        
        if self.custom_pokemon_list:
            pokemon_index = days_since_start % len(self.custom_pokemon_list)
            return self.custom_pokemon_list[pokemon_index]
        else:
            start_index = self.find_pokemon_index(self.start_pokemon_id)
            
            if self.cycle_all_pokemon:
                actual_index = (start_index + days_since_start) % len(self.pokemon_data)
                return self.pokemon_data[actual_index]
            else:
                actual_index = min(start_index + days_since_start, len(self.pokemon_data) - 1)
                return self.pokemon_data[actual_index]

    def load_sprite_art(self, sprite_path):
        """Load a sprite with its transparent margin trimmed (so the art, not its canvas, is sized), or None"""
        if not sprite_path or not os.path.exists(sprite_path):
            return None
        try:
            sprite = Image.open(sprite_path)
            if sprite.mode != 'RGBA' and (sprite.mode in ('P', 'LA') or 'transparency' in sprite.info):
                sprite = sprite.convert('RGBA')
            if sprite.mode == 'RGBA' and sprite.getchannel('A').getextrema()[0] < 255:
                art = sprite.getchannel('A').getbbox()
            else:
                # No transparency (Gen 1 and 2 sprites): the margin is plain white instead
                rgb = sprite.convert('RGB')
                art = ImageChops.difference(rgb, Image.new('RGB', rgb.size, WHITE)).getbbox()
            if art:
                sprite = sprite.crop(art)
            sprite.load()
            return sprite
        except Exception as e:
            logging.warning(f"Failed to load sprite {sprite_path}: {e}")
            return None

    def scale_sprite_for_display(self, art, scale):
        """Scale sprite art and process it for e-ink, or None.

        Whole-number scales use nearest-neighbour, which keeps every sprite pixel the same
        size. Anything else is art that has to shrink, which cannot be done pixel-perfectly.
        """
        try:
            resample = Image.Resampling.NEAREST if scale == int(scale) else Image.Resampling.LANCZOS
            new_size = (max(1, int(art.width * scale)), max(1, int(art.height * scale)))
            logging.info(f"Sprite scaling: art {art.width}x{art.height}, scale {scale:.3f}, "
                         f"final {new_size[0]}x{new_size[1]}")
            # Dither after scaling, so the e-ink pattern is at display resolution
            return self.enhance_sprite_for_eink(art.resize(new_size, resample))
        except Exception as e:
            logging.warning(f"Failed to scale sprite: {e}")
            return None

    @staticmethod
    def text_cut_off(layout):
        """True if any text in a computed layout would be drawn clipped.

        picolay shrinks text to fit, but stops at min_font_size and clips what is left over.
        """
        def cut(box):
            node, style = box.node, box.node.style
            if isinstance(node, Text) and node.content:
                size = box.resolved_font_size or style.font_size
                width = box.width - style.h_padding - style.h_border + 0.5
                font = font_for(style, size)
                if box.lines is not None:
                    height = box.height - style.v_padding - style.v_border + 0.5
                    if len(box.lines) * line_pitch(style, size) > height:
                        return True
                    if any(text_width(font, line) > width for line in box.lines):
                        return True
                elif text_width(font, node.content) > width:
                    return True
            return any(cut(child) for child in box.children)
        with measuring(False):
            return cut(layout)

    @staticmethod
    def sparkle_image(r, fill, outline):
        """A 4-point sparkle star with an outline, on a transparent square"""
        size = 2 * (r + 3) + 1
        image = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        cx = cy = r + 3

        def star(radius):
            n = radius * 0.28  # pinch of the inner points
            return [(cx, cy - radius), (cx + n, cy - n), (cx + radius, cy), (cx + n, cy + n),
                    (cx, cy + radius), (cx - n, cy + n), (cx - radius, cy), (cx - n, cy - n)]
        draw.polygon(star(r + 3), fill=outline + (255,))
        draw.polygon(star(r), fill=fill + (255,))
        return image

    def type_colors(self, type_name):
        """(fill, ink) of a type's chip; the panel's own solid colours, or black and white"""
        fill, ink = TYPE_CHIPS.get(str(type_name).lower(), (WHITE, BLACK))
        if self.color_mode != '7color':
            fill, ink = (WHITE, BLACK) if fill == WHITE else (BLACK, WHITE)
        return fill, ink

    def chip(self, label, fill, ink, font_size):
        """A small solid label; a white fill gets an outline in the ink colour"""
        border = max(2, round(font_size / 7))
        return Box(
            style=Style(width='fit', height='fit', background=fill, border=(border, ink if fill == WHITE else fill),
                        padding=(round(font_size * 0.3), round(font_size * 0.6))),
            children=[Text(label, style=Style(font_size=font_size, font_color=ink, font_path=self.font_bold))],
        )

    @staticmethod
    def rows_overflow(layout):
        """True if a row, or a column of fixed height, in a computed layout holds more than fits in it"""
        def over(box):
            style = box.node.style
            if isinstance(box.node, Box) and box.children:
                row = style.flex_direction != 'column'
                gaps = style.gap * (len(box.children) - 1)
                if row:
                    need = sum(c.width + c.node.style.margin_left + c.node.style.margin_right for c in box.children)
                    room = box.width - style.h_padding - style.h_border
                elif isinstance(style.height, (int, float)):
                    need = sum(c.height + c.node.style.margin_top + c.node.style.margin_bottom for c in box.children)
                    room = box.height - style.v_padding - style.v_border
                else:
                    need = room = gaps = 0  # a column sized to its content cannot overflow itself
                if need + gaps > room + 0.5:
                    return True
            return any(over(child) for child in box.children)
        return over(layout)

    def display_date(self):
        """The date shown on the frame"""
        return datetime.now()

    def create_display_image(self):
        """Create the e-ink display image: the sprite on the left; a calendar-leaf date,
        the Pokemon's number, name and types and its Pokedex entry on the right.

        Layout is done by picolay. Every size derives from the content height, so the
        same design holds from a 100px-inset 800x480 panel up to a bare 880x528 one.
        Flat areas use only the panel's own colours, which it shows without dithering.
        """
        base_W, base_H = self.display_width, self.display_height  # e.g. 880 × 528

        # Apply border inset configuration
        border_inset = self.config.get('display', {}).get('border_inset', {})
        inset_enabled = border_inset.get('enabled', True)
        inset_pixels = max(0, min(100, border_inset.get('pixels', 0)))  # clamp to 0-100

        # Effective canvas size with inset
        if inset_enabled and inset_pixels > 0:
            W = max(400, base_W - 2 * inset_pixels)  # minimum size to keep the layout viable
            H = max(300, base_H - 2 * inset_pixels)
        else:
            W, H = base_W, base_H
            inset_pixels = 0

        # picolay draws in RGB; monochrome output is converted to 1-bit at the end
        color = self.color_mode == '7color'
        regular, bold = self.font_regular, self.font_bold

        scale = max(0.78, min(1.35, H / 360))  # 1.0 on the 800x480 panel with a 60px inset

        def u(pixels):
            return max(1, round(pixels * scale))

        pad, column_gap = u(20), u(26)
        sprite_margin = u(5)                               # breathing room left of the sprite
        column_height = H - 2 * pad                        # the text column; the sprite may use all of H
        sprite_column = min(column_height, int(W * 0.46))  # the sprite column is never narrower than this
        min_info_width = u(208)                            # nor does it leave the text less than this

        pokemon = self.get_current_pokemon()
        shiny_today = self.is_shiny(pokemon)
        types = pokemon.get('types') or []

        # The day's accent is the colour of the Pokemon's first type
        accent, on_accent = self.type_colors(types[0]) if types else (BLACK, WHITE)
        if accent == WHITE:
            accent, on_accent = on_accent, WHITE
        accent_ink = BLACK if accent == YELLOW else accent  # yellow is unreadable as text

        # Sprite
        sprite_path = pokemon.get('local_sprite')
        if shiny_today:
            sprite_path = pokemon['local_shiny_sprite']
            logging.info(f"✨ Shiny {pokemon['name']} today!")
        art = self.load_sprite_art(sprite_path)

        def info_column(info_width):
            """(node, name, entry): the text column at a width, and its two shrinkable texts"""
            # Date: the day of the month on a calendar leaf, beside the weekday and the month and year
            now = self.display_date()
            leaf = u(58)
            date_children = [
                Box(style=Style(width=leaf, height=leaf, background=accent, justify_content='center', align_items='center'),
                    children=[Text(str(now.day), style=Style(font_size=u(34), font_color=on_accent, font_path=bold))]),
                Box(style=Style(flex_grow=1, height='fit', flex_direction='column', gap=u(9)),
                    children=[
                        Text(now.strftime("%A"), style=Style(font_size=u(27), font_path=bold, font_shrink=True,
                                                             min_font_size=u(16))),
                        Text(now.strftime("%B %Y"), style=Style(font_size=u(17), font_path=regular)),
                    ]),
            ]
            date_row = Box(style=Style(height='fit', align_items='center', gap=u(14)), children=date_children)
            rule = Box(style=Style(height=max(2, u(2)), background=BLACK))

            # Dex number (and a sparkle when shiny) over the name, which shrinks to fit
            pokemon_name = pokemon.get('name', f"Pokemon #{pokemon['id']:03d}")
            number_children = [Text(f"No. {pokemon['id']:03d}",
                                    style=Style(font_size=u(15), font_color=accent_ink, font_path=bold))]
            if shiny_today:
                number_children += [
                    Img(self.sparkle_image(u(7), YELLOW, BLACK)),
                    Text("SHINY", style=Style(font_size=u(15), font_path=bold)),
                ]
            if self.demo_mode:
                number_children += [Box(style=Style(flex_grow=1, height=1)), self.chip("DEMO MODE", WHITE, BLACK, u(10))]
            name_text = Text(pokemon_name, style=Style(font_size=u(44), min_font_size=u(20), font_shrink=True,
                                                       font_path=bold, margin=(u(9), 0, 0, 0)))
            entry_children = [
                Box(style=Style(height='fit', align_items='center', gap=u(6)), children=number_children),
                name_text,
            ]
            if types:
                entry_children.append(Box(
                    style=Style(height='fit', gap=u(8), margin=(u(12), 0, 0, 0)),
                    children=[self.chip(t.upper(), *self.type_colors(t), u(13)) for t in types],
                ))
            entry = Box(style=Style(height='fit', flex_direction='column'), children=entry_children)

            section_gap = u(15)
            heading = Box(style=Style(height='fit', flex_direction='column', gap=section_gap),
                          children=[date_row, rule, entry])
            info_children = [heading]

            # Pokedex entry: wrapped, and shrunk (never truncated) to what is left of the column
            pokedex_text = get_pokedex_description(pokemon['id']) if pokemon.get('id') else None
            entry_text = None
            if pokedex_text:
                heading_height = compute_layout(heading, info_width, column_height, antialias=False).height
                entry_text = Text(pokedex_text, style=Style(
                    wrap=True, font_shrink=True, font_size=u(19), min_font_size=8, line_spacing=u(6), font_path=regular,
                    max_height=max(2, int(column_height - heading_height - section_gap))))
                info_children.append(entry_text)
            node = Box(style=Style(width=info_width, height=column_height, flex_direction='column',
                                   justify_content='center', gap=section_gap),
                       children=info_children)
            return node, name_text, entry_text

        def info_fits(info_width, floors=None):
            """(node, sizes) if no text is cut off at this width and none is below its floor, else None"""
            node, name_text, entry_text = info_column(info_width)
            layout = compute_layout(node, info_width, column_height, antialias=False)
            if self.text_cut_off(layout) or self.rows_overflow(layout):
                return None
            sizes = {}

            def collect(box):
                if box.node is name_text or box.node is entry_text:
                    sizes[box.node is name_text] = box.resolved_font_size or box.node.style.font_size
                for child in box.children:
                    collect(child)
            collect(layout)
            if floors and any(sizes[key] < floors[key] for key in sizes):
                return None
            return node, sizes

        def info_width_beside(column):
            return W - sprite_margin - pad - column_gap - column

        # The sprite takes the largest whole-number scale the frame allows, as long as the text
        # beside it is not cut off and stays readable. The text always wins: a sprite that would
        # squeeze it steps down a scale.
        sprite_node, column, info, sprite_scale = None, sprite_column, None, None
        if art is not None:
            base_fit = min(sprite_column / art.width, H / art.height)
            if base_fit < 1:
                scales = [base_fit]  # larger than the column: it can only shrink
            else:
                most = int(min(info_width_beside(0) - min_info_width, W) / art.width)
                most = max(int(base_fit), min(most, int(H / art.height)))
                scales = list(range(most, int(base_fit) - 1, -1))
            base = info_fits(info_width_beside(sprite_column))
            # Text may shrink to make room, but not below these sizes (or what it would be anyway)
            floors = {True: u(25), False: u(12)}
            if base:
                floors = {key: min(floors[key], size) for key, size in base[1].items()}
            for sprite_scale in scales:
                column = max(sprite_column, int(art.width * sprite_scale))
                fitted = info_fits(info_width_beside(column), floors)
                if fitted or sprite_scale == scales[-1]:
                    break
            sprite = self.scale_sprite_for_display(art, sprite_scale)
            if sprite is not None:
                # Already scaled and dithered: place it at natural size, never resampled
                sprite_node = Img(sprite, style=Style(width=column, height=H, object_fit='none'))
        if sprite_node is None:
            column = sprite_column
            placeholder = min(sprite_column, column_height) - u(80)
            sprite_node = Box(
                style=Style(width=column, height=H, justify_content='center', align_items='center'),
                children=[Box(
                    style=Style(width=placeholder, height=placeholder, border=(3, BLACK),
                                justify_content='center', align_items='center'),
                    children=[Text("No Sprite", style=Style(font_size=u(20), font_path=regular))],
                )],
            )
        info = info_column(info_width_beside(column))[0]

        tree = Box(
            style=Style(width=W, height=H, padding=(0, pad, 0, sprite_margin), gap=column_gap, align_items='center'),
            children=[sprite_node, info],
        )
        # Hard-edged text: the panels have no greys, so smoothed edges would only be dithered into speckle
        content_image = picolay_render(tree, W, H, antialias=False)
        logging.info(f"Pokemon display: No. {pokemon['id']:03d} '{pokemon.get('name')}', content {W}x{H}, scale {scale:.2f}, "
                     f"sprite x{sprite_scale} in a {column}px column, text {info_width_beside(column)}px")

        if not color:
            # 1-bit panels: threshold whatever is not already black or white
            content_image = content_image.convert('L').point(lambda p: 255 if p >= 128 else 0, mode='1')

        if inset_pixels > 0:
            image = Image.new(content_image.mode, (base_W, base_H), (255, 255, 255) if color else 255)
            image.paste(content_image, (inset_pixels, inset_pixels))
            logging.info(f"Applied border inset: {inset_pixels}px, content size: {W}x{H}, final size: {base_W}x{base_H}")
        else:
            image = content_image
        return image

    def update_display(self, force_full_refresh=False):
        """Update the e-ink display with current Pokemon (includes e-Paper safety checks)"""
        try:
            # E-Paper safety check: minimum refresh interval
            if not self.can_refresh_display():
                return False
            
            current_pokemon = self.get_current_pokemon()
            logging.info(f"Updating display with Pokemon: {current_pokemon['name']} (ID: {current_pokemon['id']})")
            
            # Create the display image
            image = self.create_display_image()
            
            # Always save a preview image for web UI, regardless of simulation mode
            if self.color_mode == '7color':
                preview_path = self.cache_dir / "current_display_7color.png"
                logging.info(f"7-color display preview saved to {preview_path}")
            else:
                preview_path = self.cache_dir / "current_display.png"
                logging.info(f"Monochrome display preview saved to {preview_path}")
            
            image.save(preview_path)
            
            if self.epd:
                # All Waveshare library calls do full refreshes (no partial refresh support)
                force_full_refresh = force_full_refresh or self.needs_full_refresh()
                current_time = time.time()
                
                logging.info("E-Paper safety: Performing FULL refresh (Waveshare library only supports full refreshes)")
                
                # Set border color before display update
                self.set_border_color()
                
                # Update real e-ink display based on type
                if self.epd_type == '7in3e':
                    # 7-color display requires specific buffer format
                    self.epd.display(self.epd.getbuffer(image))
                    logging.info("7-color display updated successfully")
                else:
                    # Standard monochrome display (7in5_HD)
                    self.epd.display(self.epd.getbuffer(image))
                    logging.info("Monochrome display updated successfully")
                
                # Update safety tracking
                self.last_refresh_time = current_time
                self.last_full_refresh = current_time  # Always full refresh
                
            else:
                logging.info("Running in simulation mode - no hardware display available")
            
            # Notify web server if available - ALWAYS send current state
            if self.web_server:
                try:
                    # Get fresh current Pokemon data to ensure accuracy
                    fresh_current_pokemon = self.get_current_pokemon()
                    current_data = {
                        "type": "display_updated",
                        "data": {
                            "current_pokemon": fresh_current_pokemon,
                            "demo_mode": self.demo_mode,
                            "current_pokemon_index": getattr(self, 'current_pokemon_index', 0),
                            "timestamp": datetime.now().isoformat()
                        }
                    }
                    
                    # Add to queue for WebSocket broadcasting
                    if hasattr(self.web_server, 'websocket_manager'):
                        # Store the message to be sent
                        if not hasattr(self.web_server, '_pending_messages'):
                            self.web_server._pending_messages = []
                        self.web_server._pending_messages.append(current_data)
                        logging.info(f"Added display update message to WebSocket queue: {fresh_current_pokemon['name']} (ID: {fresh_current_pokemon['id']})")
                    
                except Exception as e:
                    logging.warning(f"Failed to queue web server notification: {e}")
            
            return True
                
        except Exception as e:
            logging.error(f"Failed to update display: {e}")
            return False

    def demo_cycle(self):
        """Cycle to next Pokemon in demo mode (respects e-Paper safety rules)"""
        if self.demo_mode:
            # Generate random Pokemon index (0-based array index, not Pokemon ID)
            max_index = len(self.pokemon_data) - 1
            self.current_pokemon_index = random.randint(0, max_index)
            
            current_pokemon = self.get_current_pokemon()
            logging.info(f"Demo cycle: Random Pokemon selected - #{current_pokemon['id']:03d} {current_pokemon['name']} (index: {self.current_pokemon_index})")
            
            # Try to update display (will be blocked by safety checks if too frequent)
            success = self.update_display()
            if not success:
                logging.info("Demo cycle: Display update blocked by e-Paper safety rules, but Pokemon changed for next refresh")
    
    def set_demo_mode(self, enabled):
        """Set demo mode and handle state transitions"""
        old_mode = self.demo_mode
        self.demo_mode = enabled
        
        if old_mode != enabled:
            if enabled:
                # Entering demo mode - start with a random Pokemon
                logging.info("Entering demo mode - selecting random Pokemon")
                max_index = len(self.pokemon_data) - 1
                self.current_pokemon_index = random.randint(0, max_index)
            else:
                # Exiting demo mode - reset to normal date-based Pokemon
                logging.info("Exiting demo mode - resetting to date-based Pokemon")
                self.current_pokemon_index = 0  # Reset index for normal mode
            
            # Force display update to reflect mode change
            self.update_display()
            logging.info(f"Demo mode changed: {old_mode} → {enabled}")
        
        return old_mode

    def midnight_update(self):
        """Update display at midnight (normal mode) - force full refresh daily"""
        if not self.demo_mode:
            logging.info("Midnight update triggered - forcing full refresh")
            self.update_display(force_full_refresh=True)

    def run(self):
        """Main run loop with dynamic demo mode switching"""
        logging.info("Starting Pokemon E-ink Calendar")
        
        # Start web server if enabled
        if self.enable_web_server and self.web_server:
            try:
                self.web_server.start()
                logging.info(f"Web server started at http://{self.web_host}:{self.web_port}")
            except Exception as e:
                logging.error(f"Failed to start web server: {e}")
        
        # Initial display update
        self.update_display()
        
        # Unified run loop that dynamically switches between demo and normal mode
        logging.info("Starting unified run loop with dynamic mode switching")
        
        # Schedule midnight updates for normal mode
        schedule.every().day.at("00:00").do(self.midnight_update)

        last_demo_check = 0
        
        while True:
            current_time = time.time()
            
            if self.demo_mode:
                # Demo mode: cycle every 30 seconds
                if current_time - last_demo_check >= 30:
                    self.demo_cycle()
                    last_demo_check = current_time
                time.sleep(5)  # Check more frequently in demo mode
            else:
                # Normal mode: run scheduled tasks
                schedule.run_pending()
                time.sleep(60)  # Check every minute in normal mode

    def cleanup(self):
        """Clean up resources and prepare display for storage"""
        logging.info("Cleaning up Pokemon E-ink Calendar...")
        
        if self.web_server:
            try:
                self.web_server.stop()
                logging.info("Web server stopped")
            except:
                pass
        
        if self.epd:
            try:
                # Prepare display for storage (clear screen multiple times)
                self.prepare_for_storage()
            except:
                try:
                    # Fallback: just put to sleep
                    self.epd.sleep()
                    logging.info("E-ink display put to sleep")
                except:
                    pass

def main():
    parser = argparse.ArgumentParser(description="Pokemon E-ink Calendar for Raspberry Pi")
    parser.add_argument("--demo", action="store_true", help="Run in demo mode (respects 180s e-Paper safety interval)")
    parser.add_argument("--cache-dir", default=None, help="Directory for caching Pokemon data and sprites")
    parser.add_argument("--config", default="./config.json", help="Configuration file path")
    parser.add_argument("--update-only", action="store_true", help="Update display once and exit")
    parser.add_argument("--show-schedule", type=int, metavar="DAYS", help="Show Pokemon schedule for next N days")
    parser.add_argument("--preview", action="store_true", help="Generate preview PNG and exit (no e-ink display needed)")
    parser.add_argument("--pokemon", type=int, metavar="ID", help="Specific Pokemon ID for preview (1-1025). Use with --preview to see any Pokemon.")
    parser.add_argument("--web-server", action="store_true", help="Enable web server for remote control")
    parser.add_argument("--web-host", default="0.0.0.0", help="Web server host (default: 0.0.0.0)")
    parser.add_argument("--web-port", type=int, default=8000, help="Web server port (default: 8000)")
    parser.add_argument("--prepare-storage", action="store_true", help="Clear screen multiple times and prepare display for long-term storage")
    
    args = parser.parse_args()
    
    # Validate argument combinations
    if args.pokemon and not args.preview:
        print("❌ Error: --pokemon can only be used with --preview")
        print("Example: python3 pokemon_eink_calendar.py --preview --pokemon 25")
        return
    
    calendar = None
    try:
        calendar = PokemonEInkCalendar(
            demo_mode=args.demo, 
            cache_dir=args.cache_dir,
            config_file=args.config,
            enable_web_server=args.web_server,
            web_host=args.web_host,
            web_port=args.web_port
        )
        
        if args.prepare_storage:
            # Prepare display for long-term storage
            print("🔧 Preparing e-Paper display for long-term storage...")
            print("This will clear the screen multiple times as recommended by the manufacturer.")
            calendar.prepare_for_storage()
            print("✅ Display prepared for storage and put to sleep")
            return
        
        if args.show_schedule:
            # Show Pokemon schedule for planning
            print(f"\n🗓️  Pokemon Schedule (next {args.show_schedule} days):")
            print("=" * 50)
            
            today = datetime.now().date()
            for i in range(args.show_schedule):
                future_date = today + timedelta(days=i)
                pokemon = calendar.get_pokemon_info_for_date(future_date)
                day_name = future_date.strftime("%A")
                date_str = future_date.strftime("%Y-%m-%d")
                
                if i == 0:
                    print(f"📅 {day_name}, {date_str} → #{pokemon['id']:03d} {pokemon['name']} ⭐ (TODAY)")
                else:
                    print(f"📅 {day_name}, {date_str} → #{pokemon['id']:03d} {pokemon['name']}")
            
            print("=" * 50)
            return
        
        if args.preview:
            # Generate preview PNG and exit
            preview_pokemon = None
            
            if args.pokemon:
                # Use specific Pokemon ID if provided
                if 1 <= args.pokemon <= 1025:
                    preview_pokemon = calendar.get_pokemon_by_id(args.pokemon)
                    if not preview_pokemon:
                        print(f"❌ Pokemon #{args.pokemon} not found in loaded data")
                        print("Available Pokemon IDs: 1-1025")
                        return
                else:
                    print(f"❌ Invalid Pokemon ID: {args.pokemon}")
                    print("Valid range: 1-1025")
                    return
            else:
                # Use current Pokemon (based on date/demo mode)
                preview_pokemon = calendar.get_current_pokemon()
            
            # Temporarily override the current Pokemon for preview
            original_get_current = calendar.get_current_pokemon
            calendar.get_current_pokemon = lambda: preview_pokemon
            
            # Generate the image
            image = calendar.create_display_image()
            preview_path = Path("preview_display.png")
            image.save(preview_path)
            
            # Restore original method
            calendar.get_current_pokemon = original_get_current
            
            print(f"✅ Preview image generated: {preview_path}")
            print(f"🔥 Pokemon: #{preview_pokemon['id']:03d} {preview_pokemon['name']}")
            if 'types' in preview_pokemon:
                types_display = '/'.join(t.title() for t in preview_pokemon['types'])
                print(f"⚡ Types: {types_display}")
            if 'generation' in preview_pokemon:
                print(f"🎮 Generation: {preview_pokemon['generation']}")
            return
        
        if args.update_only:
            calendar.update_display()
            logging.info("Single update completed")
        else:
            calendar.run()
            
    except KeyboardInterrupt:
        logging.info("Received interrupt signal, shutting down...")
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
    finally:
        if calendar:
            calendar.cleanup()

if __name__ == "__main__":
    main()