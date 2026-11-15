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
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter
import argparse
from pathlib import Path
import schedule
import threading
import numpy as np
import random
import hashlib

# Import Pokemon data with types and generations
from pokemon_data_with_types import POKEMON_DATA, get_pokemon_info, get_pokemon_types, get_pokemon_generation
from type_icons import get_all_type_icons_for_pokemon
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
        
        # Load fonts (fallback to default if Font.ttc not available)
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
        """Load fonts for display text with exact specifications"""
        font_paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/System/Library/Fonts/Helvetica.ttc",  # macOS
            "/home/pi/e-Paper/RaspberryPi_JetsonNano/python/pic/Font.ttc"  # Waveshare default
        ]
        
        # Font specifications per the design spec
        self.font_date_weekday = None      # 32px for weekday
        self.font_date_full = None         # 24px for full date
        self.font_dex_number = None        # 24px medium for dex number
        self.font_name_primary = None      # 52px bold for Pokemon name (will auto-shrink)
        self.font_type_pills = None        # 18px for type pills
        self.font_flavor_text = None       # 22px for flavor text (will auto-shrink)
        
        for font_path in font_paths:
            if os.path.exists(font_path):
                try:
                    # Load all required font sizes
                    self.font_date_weekday = ImageFont.truetype(font_path, 32)
                    self.font_date_full = ImageFont.truetype(font_path, 24)
                    self.font_dex_number = ImageFont.truetype(font_path, 24)
                    self.font_name_primary = ImageFont.truetype(font_path, 52)
                    self.font_type_pills = ImageFont.truetype(font_path, 18)
                    self.font_flavor_text = ImageFont.truetype(font_path, 22)
                    logging.info(f"Loaded spec fonts from {font_path}")
                    break
                except Exception as e:
                    logging.warning(f"Failed to load font {font_path}: {e}")
        
        # Fallback to default font
        if not self.font_date_weekday:
            try:
                default_font = ImageFont.load_default()
                self.font_date_weekday = default_font
                self.font_date_full = default_font
                self.font_dex_number = default_font
                self.font_name_primary = default_font
                self.font_type_pills = default_font
                self.font_flavor_text = default_font
                logging.info("Using default fonts")
            except:
                logging.error("Failed to load any fonts")

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

    def wrap_text(self, text, font, max_width):
        """Wrap text to fit within a given width"""
        words = text.split(' ')
        lines = []
        current_line = ""
        
        for word in words:
            # Test if adding this word would exceed the width
            test_line = current_line + " " + word if current_line else word
            
            # Create a temporary draw object to measure text
            temp_img = Image.new('1', (1, 1))
            temp_draw = ImageDraw.Draw(temp_img)
            bbox = temp_draw.textbbox((0, 0), test_line, font=font)
            text_width = bbox[2] - bbox[0]
            
            if text_width <= max_width:
                current_line = test_line
            else:
                # Add current line to lines and start new line
                if current_line:
                    lines.append(current_line)
                current_line = word
        
        # Add the last line if it exists
        if current_line:
            lines.append(current_line)
        
        return lines
    
    def wrap_text_improved(self, text, font, max_width):
        """Improved text wrapping with better word breaking and hyphenation support"""
        words = text.split(' ')
        lines = []
        current_line = ""
        
        # Create a temporary draw object to measure text
        temp_img = Image.new('1', (1, 1))
        temp_draw = ImageDraw.Draw(temp_img)
        
        for word in words:
            # Test if adding this word would exceed the width
            test_line = current_line + " " + word if current_line else word
            bbox = temp_draw.textbbox((0, 0), test_line, font=font)
            text_width = bbox[2] - bbox[0]
            
            if text_width <= max_width:
                current_line = test_line
            else:
                # If current line has content, save it and start new line
                if current_line:
                    lines.append(current_line)
                    current_line = word
                else:
                    # Single word is too long, try to break it
                    if len(word) > 8:  # Only break long words
                        # Try to fit as much as possible with hyphen
                        for i in range(len(word) - 1, 0, -1):
                            test_word = word[:i] + "-"
                            bbox = temp_draw.textbbox((0, 0), test_word, font=font)
                            if bbox[2] - bbox[0] <= max_width:
                                lines.append(test_word)
                                current_line = word[i:]
                                break
                        else:
                            # If we can't break it, just add the word as is
                            current_line = word
                    else:
                        current_line = word
        
        # Add the last line if it exists
        if current_line:
            lines.append(current_line)
        
        return lines

    def add_generation_authentic_type_icons(self, image, pokemon, area_x, area_y, area_width):
        """Add authentic generation-specific Pokemon type icons to the display (centered)"""
        try:
            # Get type information
            pokemon_types = pokemon.get('types', [])
            pokemon_generation = pokemon.get('generation', 1)
            
            if not pokemon_types:
                return False
            
            # Get generation-specific type icon paths
            type_icon_paths = get_all_type_icons_for_pokemon(
                pokemon_types, 
                pokemon_generation, 
                "types"
            )
            
            if not type_icon_paths:
                logging.warning(f"No generation-{pokemon_generation} type icons found for {pokemon['name']}: {pokemon_types}")
                return False
            
            # Load and process type icons with authentic scaling
            type_icons = []
            total_width = 0
            
            for icon_path in type_icon_paths:
                try:
                    if icon_path.exists():
                        type_icon = Image.open(icon_path)
                        
                        # Scale type icons for e-ink visibility while preserving authenticity
                        target_height = 32  # Good balance between authenticity and visibility
                        scale_factor = target_height / type_icon.height
                        new_width = int(type_icon.width * scale_factor)
                        new_height = target_height
                        
                        # Use nearest neighbor for pixel-perfect authentic scaling
                        type_icon = type_icon.resize((new_width, new_height), Image.Resampling.NEAREST)
                        
                        # Apply specialized e-ink processing for type icons
                        type_icon = self.enhance_sprite_for_eink(type_icon)
                        
                        type_icons.append(type_icon)
                        total_width += new_width
                        if len(type_icons) > 1:
                            total_width += 8  # Gap between icons
                        
                        logging.info(f"Loaded authentic Gen-{pokemon_generation} type icon: {icon_path.name} -> {new_width}x{new_height}")
                    
                except Exception as e:
                    logging.warning(f"Failed to load type icon {icon_path}: {e}")
            
            if not type_icons:
                return False
            
            # Calculate centered positioning for type icons
            start_x = area_x - (total_width // 2)  # Center relative to area_x
            
            # Paste type icons onto the main image
            current_x = start_x
            for type_icon in type_icons:
                image.paste(type_icon, (current_x, area_y))
                current_x += type_icon.width + 8  # 8px gap between icons
            
            logging.info(f"Added {len(type_icons)} centered authentic Gen-{pokemon_generation} type icons for {pokemon['name']}: {', '.join(pokemon_types)}")
            return True
            
        except Exception as e:
            logging.error(f"Failed to add authentic type icons for {pokemon['name']}: {e}")
            return False

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

    @staticmethod
    def draw_sparkle(draw, cx, cy, r, fill, outline):
        """Draw a 4-point sparkle star centered at (cx, cy) with an outline"""
        def star(radius):
            n = radius * 0.28  # pinch of the inner points
            return [(cx, cy - radius), (cx + n, cy - n), (cx + radius, cy), (cx + n, cy + n),
                    (cx, cy + radius), (cx - n, cy + n), (cx - radius, cy), (cx - n, cy - n)]
        draw.polygon(star(r + 3), fill=outline)
        draw.polygon(star(r), fill=fill)

    def create_display_image(self):
        """Create pixel-perfect e-ink display image with distinct sections"""
        # Canvas & grid - exact specifications
        base_W, base_H = self.display_width, self.display_height  # 880 × 528
        
        # Apply border inset configuration
        border_inset = self.config.get('display', {}).get('border_inset', {})
        inset_enabled = border_inset.get('enabled', True)
        inset_pixels = border_inset.get('pixels', 0)
        
        # Clamp inset pixels to valid range (0-100)
        inset_pixels = max(0, min(100, inset_pixels))
        
        # Calculate effective canvas size with inset
        if inset_enabled and inset_pixels > 0:
            W = base_W - (2 * inset_pixels)
            H = base_H - (2 * inset_pixels)
            # Ensure minimum viable canvas size
            W = max(400, W)  # Minimum width to maintain layout
            H = max(300, H)  # Minimum height to maintain layout
        else:
            W, H = base_W, base_H
            inset_pixels = 0
        
        M = 24  # Safe margin
        G = 24  # Column gap
        SEPARATOR_HEIGHT = 2  # Height of section separators
        SECTION_GAP = 16  # Gap between sections
        
        # Create blank white canvas (full size) - mode depends on display type
        if self.color_mode == '7color':
            image = Image.new('RGB', (base_W, base_H), (255, 255, 255))  # White RGB canvas
        else:
            image = Image.new('1', (base_W, base_H), 255)  # 1-bit white canvas for monochrome
        
        # Create the actual display content on effective canvas size
        if self.color_mode == '7color':
            content_image = Image.new('RGB', (W, H), (255, 255, 255))  # White RGB content
        else:
            content_image = Image.new('1', (W, H), 255)  # 1-bit white content for monochrome
        draw = ImageDraw.Draw(content_image)
        
        # Define colors based on display mode
        if self.color_mode == '7color':
            BLACK = (0, 0, 0)
            WHITE = (255, 255, 255)
            LIGHT_GRAY = (200, 200, 200)
        else:
            BLACK = 0
            WHITE = 255
            LIGHT_GRAY = 0  # Use black for separators in monochrome
        
        # Get current Pokemon
        pokemon = self.get_current_pokemon()
        
        # Define three distinct sections with improved layout
        sprite_box_width = int(W * 0.51)  # Maintain proportion
        sprite_box_height = int(H * 0.85)  # Maintain proportion
        sprite_box_size = min(sprite_box_width, sprite_box_height)
        
        SPRITE_BOX = {'x': M, 'y': M, 'w': sprite_box_size, 'h': sprite_box_size}
        
        # Calculate right column areas with distinct sections
        right_column_x = M + SPRITE_BOX['w'] + G
        right_column_width = W - right_column_x - M
        
        # Section 1: Date (top 25% of right column)
        DATE_SECTION = {
            'x': right_column_x,
            'y': M,
            'w': right_column_width,
            'h': int((H - 2*M) * 0.25)
        }
        
        # Section 2: Pokemon Info (next 35% of right column)
        INFO_SECTION = {
            'x': right_column_x,
            'y': DATE_SECTION['y'] + DATE_SECTION['h'] + SECTION_GAP,
            'w': right_column_width,
            'h': int((H - 2*M) * 0.35)
        }
        
        # Section 3: Description (remaining space)
        DESC_SECTION = {
            'x': right_column_x,
            'y': INFO_SECTION['y'] + INFO_SECTION['h'] + SECTION_GAP,
            'w': right_column_width,
            'h': H - M - (INFO_SECTION['y'] + INFO_SECTION['h'] + SECTION_GAP)
        }
        
        # SPRITE - Load and process Pokemon sprite
        sprite = None
        sprite_path = pokemon.get('local_sprite')
        shiny_today = self.is_shiny(pokemon)
        if shiny_today:
            sprite_path = pokemon['local_shiny_sprite']
            logging.info(f"✨ Shiny {pokemon['name']} today!")
        if sprite_path and os.path.exists(sprite_path):
            try:
                sprite = Image.open(sprite_path)
                
                # Scale to fit sprite box (preserve aspect ratio) - now proportional to canvas
                scale = min(SPRITE_BOX['w'] / sprite.width, SPRITE_BOX['h'] / sprite.height)
                new_width = int(sprite.width * scale)
                new_height = int(sprite.height * scale)
                
                logging.info(f"Sprite scaling: original {sprite.width}x{sprite.height}, sprite_box {SPRITE_BOX['w']}x{SPRITE_BOX['h']}, scale {scale:.3f}, final {new_width}x{new_height}")
                
                # Use appropriate resampling based on original size
                if sprite.width <= 96 and sprite.height <= 96:
                    sprite = sprite.resize((new_width, new_height), Image.Resampling.NEAREST)
                else:
                    sprite = sprite.resize((new_width, new_height), Image.Resampling.LANCZOS)
                
                # Apply e-ink processing
                sprite = self.enhance_sprite_for_eink(sprite)
                
                # Center sprite in sprite box
                sprite_x = SPRITE_BOX['x'] + (SPRITE_BOX['w'] - sprite.width) // 2
                sprite_y = SPRITE_BOX['y'] + (SPRITE_BOX['h'] - sprite.height) // 2
                
                content_image.paste(sprite, (sprite_x, sprite_y))
                logging.info(f"Sprite centered at ({sprite_x}, {sprite_y}), size: {sprite.size}")
                
            except Exception as e:
                logging.warning(f"Failed to load sprite for {pokemon['name']}: {e}")
                sprite = None
        
        if not sprite:
            # Placeholder for missing sprite
            placeholder_size = min(SPRITE_BOX['w'], SPRITE_BOX['h']) - 80
            placeholder_x = SPRITE_BOX['x'] + (SPRITE_BOX['w'] - placeholder_size) // 2
            placeholder_y = SPRITE_BOX['y'] + (SPRITE_BOX['h'] - placeholder_size) // 2
            
            draw.rectangle([placeholder_x, placeholder_y, 
                          placeholder_x + placeholder_size, placeholder_y + placeholder_size], 
                         outline=BLACK, width=3)
            
            no_image_text = "No Sprite"
            if self.font_date_full:
                no_img_bbox = draw.textbbox((0, 0), no_image_text, font=self.font_date_full)
                no_img_width = no_img_bbox[2] - no_img_bbox[0]
                draw.text((placeholder_x + (placeholder_size - no_img_width) // 2, 
                          placeholder_y + placeholder_size // 2 - 12), 
                         no_image_text, font=self.font_date_full, fill=BLACK)
        
        # Get text content
        current_date = datetime.now()
        weekday = current_date.strftime("%A")
        full_date = current_date.strftime("%B %d, %Y")
        pokemon_name = pokemon.get('name', f"Pokemon #{pokemon['id']:03d}")
        pokemon_id_text = f"#{pokemon['id']:03d}"
        
        # SECTION 1: DATE BLOCK with elegant separator
        date_y = DATE_SECTION['y'] + 8  # Start with small padding
        
        if self.font_date_weekday:
            weekday_bbox = draw.textbbox((0, 0), weekday, font=self.font_date_weekday)
            weekday_height = weekday_bbox[3] - weekday_bbox[1]
            # Center weekday horizontally in date section
            weekday_width = weekday_bbox[2] - weekday_bbox[0]
            weekday_x = DATE_SECTION['x'] + (DATE_SECTION['w'] - weekday_width) // 2
            draw.text((weekday_x, date_y), weekday, font=self.font_date_weekday, fill=BLACK)
            date_y += weekday_height + 6
        
        if self.font_date_full:
            date_bbox = draw.textbbox((0, 0), full_date, font=self.font_date_full)
            date_height = date_bbox[3] - date_bbox[1]
            # Center full date horizontally in date section
            date_width = date_bbox[2] - date_bbox[0]
            date_x = DATE_SECTION['x'] + (DATE_SECTION['w'] - date_width) // 2
            draw.text((date_x, date_y), full_date, font=self.font_date_full, fill=BLACK)
            date_y += date_height
        
        # Add elegant separator line after date section
        separator_y = DATE_SECTION['y'] + DATE_SECTION['h']
        draw.rectangle([
            DATE_SECTION['x'] + 20, separator_y,
            DATE_SECTION['x'] + DATE_SECTION['w'] - 20, separator_y + SEPARATOR_HEIGHT
        ], fill=LIGHT_GRAY)
        
        # SECTION 2: POKEMON INFO with improved layout
        info_y = INFO_SECTION['y'] + 8  # Start with padding
        
        # Combined Pokemon number and name on same line with visual separation
        if self.font_name_primary and pokemon_name and self.font_dex_number:
            # Shiny sparkle (7-color only) sits after the name; reserve its width in the layout
            SPARKLE_R = 14
            badge_width = (8 + 2 * SPARKLE_R) if shiny_today else 0

            # Calculate optimal sizes for number and name separately
            name_font = self.font_name_primary
            name_size = 52
            
            # Find the best size where both number and name fit together
            while name_size >= 28:  # Increased minimum size
                try:
                    # Use smaller font for number (about 70% of name size)
                    number_size = max(int(name_size * 0.7), 18)
                    number_font = ImageFont.truetype(self.font_dex_number.path, number_size)
                    name_test_font = ImageFont.truetype(self.font_name_primary.path, name_size)
                    
                    # Measure both parts separately
                    number_bbox = draw.textbbox((0, 0), pokemon_id_text, font=number_font)
                    name_bbox = draw.textbbox((0, 0), pokemon_name, font=name_test_font)
                    
                    # Calculate total width with spacing
                    number_width = number_bbox[2] - number_bbox[0]
                    name_width = name_bbox[2] - name_bbox[0]
                    spacing = 12  # Space between number and name
                    total_width = number_width + spacing + name_width + badge_width
                    
                    if total_width <= INFO_SECTION['w']:
                        # Found a good fit
                        break
                    
                    name_size -= 2
                except:
                    name_size -= 2
                    continue
            
            # Create the fonts
            try:
                number_size = max(int(name_size * 0.7), 18)
                number_font = ImageFont.truetype(self.font_dex_number.path, number_size)
                name_font = ImageFont.truetype(self.font_name_primary.path, name_size)
            except:
                number_font = self.font_dex_number
                name_font = self.font_name_primary
            
            # Calculate positioning for centered layout
            number_bbox = draw.textbbox((0, 0), pokemon_id_text, font=number_font)
            name_bbox = draw.textbbox((0, 0), pokemon_name, font=name_font)
            
            number_width = number_bbox[2] - number_bbox[0]
            name_width = name_bbox[2] - name_bbox[0]
            number_height = number_bbox[3] - number_bbox[1]
            name_height = name_bbox[3] - name_bbox[1]
            spacing = 12
            
            total_width = number_width + spacing + name_width + badge_width
            start_x = INFO_SECTION['x'] + (INFO_SECTION['w'] - total_width) // 2
            
            # Define colors based on display mode for visual separation
            if self.color_mode == '7color':
                NUMBER_COLOR = (100, 100, 100)  # Dark gray for number
                NAME_COLOR = (0, 0, 0)  # Black for name
            else:
                NUMBER_COLOR = BLACK  # Same color in monochrome
                NAME_COLOR = BLACK
            
            # Calculate proper baseline alignment using font metrics
            # Get the ascent and descent for both fonts to align baselines properly
            name_x = start_x + number_width + spacing
            
            # Use the name font as the baseline reference (larger font)
            name_baseline_y = info_y
            
            # Calculate where the number should be positioned to align baselines
            # The baseline is measured from the top of the bounding box
            name_ascent = name_bbox[3] - name_bbox[1] - (name_bbox[3] - name_bbox[1] - name_height)  # Approximate ascent
            number_ascent = number_bbox[3] - number_bbox[1] - (number_bbox[3] - number_bbox[1] - number_height)  # Approximate ascent
            
            # More robust approach: use the actual font metrics if available
            try:
                # Try to get actual font metrics for precise baseline alignment
                name_metrics = name_font.getmetrics()  # Returns (ascent, descent)
                number_metrics = number_font.getmetrics()
                
                # Align baselines: both fonts should have their baseline at the same Y position
                baseline_y = info_y + name_metrics[0]  # Add ascent to get baseline position
                
                # Position name at baseline
                name_y = baseline_y - name_metrics[0]  # Subtract ascent to get top position
                
                # Position number at same baseline
                number_y = baseline_y - number_metrics[0]  # Subtract ascent to get top position
                
            except (AttributeError, TypeError):
                # Fallback: estimate baseline alignment using bounding box
                # Assume baseline is roughly 80% down from the top of the character height
                baseline_offset_ratio = 0.8
                
                name_baseline_offset = int(name_height * baseline_offset_ratio)
                number_baseline_offset = int(number_height * baseline_offset_ratio)
                
                # Set name position
                name_y = info_y
                baseline_y = name_y + name_baseline_offset
                
                # Align number to same baseline
                number_y = baseline_y - number_baseline_offset
            
            number_x = start_x
            
            # Draw Pokemon number (smaller, lighter) - baseline aligned
            draw.text((number_x, number_y), pokemon_id_text, font=number_font, fill=NUMBER_COLOR)
            
            # Draw Pokemon name (larger, bolder) - baseline aligned
            draw.text((name_x, name_y), pokemon_name, font=name_font, fill=NAME_COLOR)

            # Shiny badge: a single yellow sparkle after the name
            if shiny_today:
                self.draw_sparkle(draw, name_x + name_width + 8 + SPARKLE_R,
                                  name_y + (name_bbox[1] + name_bbox[3]) // 2,
                                  SPARKLE_R, (255, 255, 0), BLACK)
            
            # Calculate the maximum height and advance position
            max_height = max(number_height, name_height)
            info_y += max_height + 12
            
            logging.info(f"Pokemon display: #{pokemon['id']:03d} at {number_size}px, '{pokemon_name}' at {name_size}px")
        elif self.font_dex_number and self.font_name_primary:
            # Fallback: separate number and name if combined doesn't work
            # Dex number (centered)
            dex_bbox = draw.textbbox((0, 0), pokemon_id_text, font=self.font_dex_number)
            dex_height = dex_bbox[3] - dex_bbox[1]
            dex_width = dex_bbox[2] - dex_bbox[0]
            dex_x = INFO_SECTION['x'] + (INFO_SECTION['w'] - dex_width) // 2
            draw.text((dex_x, info_y), pokemon_id_text, font=self.font_dex_number, fill=BLACK)
            if shiny_today:
                self.draw_sparkle(draw, dex_x + dex_width + 26, info_y + dex_height // 2, 14, (255, 255, 0), BLACK)
            info_y += dex_height + 6
            
            # Pokemon name (centered)
            name_bbox = draw.textbbox((0, 0), pokemon_name, font=self.font_name_primary)
            name_height = name_bbox[3] - name_bbox[1]
            name_width = name_bbox[2] - name_bbox[0]
            name_x = INFO_SECTION['x'] + (INFO_SECTION['w'] - name_width) // 2
            draw.text((name_x, info_y), pokemon_name, font=self.font_name_primary, fill=BLACK)
            info_y += name_height + 12
            
            logging.info(f"Separate Pokemon number and name rendered")
        
        # Type icons (centered)
        pokemon_types = pokemon.get('types', [])
        pokemon_generation = pokemon.get('generation', 1)
        if pokemon_types:
            # Calculate center position for type icons
            type_start_x = INFO_SECTION['x'] + INFO_SECTION['w'] // 2
            type_icons_added = self.add_generation_authentic_type_icons(
                content_image, pokemon, type_start_x, info_y, INFO_SECTION['w']
            )
            if type_icons_added:
                info_y += 32 + 4  # Further reduced gap to 4 pixels
                logging.info(f"Added authentic Gen-{pokemon_generation} type icons for {pokemon['name']}: {', '.join(pokemon_types)}")
            else:
                # Fallback to text (centered)
                type_text = " / ".join(t.upper() for t in pokemon_types)
                if self.font_type_pills:
                    type_bbox = draw.textbbox((0, 0), type_text, font=self.font_type_pills)
                    type_width = type_bbox[2] - type_bbox[0]
                    type_x = INFO_SECTION['x'] + (INFO_SECTION['w'] - type_width) // 2
                    draw.text((type_x, info_y), type_text, font=self.font_type_pills, fill=BLACK)
                    info_y += 24 + 4  # Further reduced gap to 4 pixels
        
        # SECTION 3: DESCRIPTION with improved text wrapping
        pokemon_id = pokemon.get('id')
        if pokemon_id and self.font_flavor_text:
            pokedex_text = get_pokedex_description(pokemon_id)
            if pokedex_text:
                # Start from where type icons/text actually ended with minimal gap
                desc_y = info_y + 4  # Use actual end position + minimal 4px gap
                available_height = DESC_SECTION['y'] + DESC_SECTION['h'] - desc_y - 8  # Calculate remaining height
                
                # Improved text sizing with better wrapping - NEVER truncate, always shrink font!
                text_size = 22
                best_font = None
                best_lines = []
                min_font_size = 8  # Allow very small text if needed
                
                while text_size >= min_font_size:
                    try:
                        if text_size != 22:
                            test_font = ImageFont.truetype(self.font_flavor_text.path, text_size)
                        else:
                            test_font = self.font_flavor_text
                    except:
                        test_font = self.font_flavor_text
                    
                    # Improved text wrapping
                    wrapped_lines = self.wrap_text_improved(pokedex_text, test_font, DESC_SECTION['w'])
                    
                    # Dynamic line height based on font size
                    line_height = max(text_size + 4, 12)  # Tighter line spacing for small text
                    total_text_height = len(wrapped_lines) * line_height
                    
                    # Check if ALL text fits at this size
                    if total_text_height <= available_height:
                        best_font = test_font
                        best_lines = wrapped_lines  # Use ALL lines, no truncation
                        break
                    
                    text_size -= 1
                
                # Last resort: if even minimum font size doesn't fit, use it anyway
                if not best_font:
                    text_size = min_font_size
                    try:
                        test_font = ImageFont.truetype(self.font_flavor_text.path, text_size)
                    except:
                        test_font = self.font_flavor_text
                    
                    wrapped_lines = self.wrap_text_improved(pokedex_text, test_font, DESC_SECTION['w'])
                    best_font = test_font
                    best_lines = wrapped_lines  # Still no truncation, render all lines
                    logging.warning(f"Using minimum font size {min_font_size}px for very long description")
                
                # Draw the COMPLETE description text - no ellipsis, no truncation
                if best_font and best_lines:
                    line_height = max(text_size + 4, 12)
                    
                    for line in best_lines:
                        if line.strip():
                            draw.text((DESC_SECTION['x'], desc_y), line, font=best_font, fill=BLACK)
                            desc_y += line_height
                    
                    logging.info(f"Complete description rendered at {text_size}px, {len(best_lines)} lines, no truncation")
                else:
                    logging.warning(f"Could not render description for Pokemon {pokemon_id} - no font available")
        
        # Demo mode indicator (small, bottom right)
        if self.demo_mode:
            demo_text = "DEMO MODE"
            if self.font_type_pills:
                demo_bbox = draw.textbbox((0, 0), demo_text, font=self.font_type_pills)
                demo_width = demo_bbox[2] - demo_bbox[0]
                draw.text((W - demo_width - M, H - 30), demo_text, font=self.font_type_pills, fill=BLACK)
        
        # Paste content image onto main image with border inset
        if inset_pixels > 0:
            # Center the content image within the border inset
            paste_x = inset_pixels
            paste_y = inset_pixels
            image.paste(content_image, (paste_x, paste_y))
            logging.info(f"Applied border inset: {inset_pixels}px, content size: {W}x{H}, final size: {base_W}x{base_H}")
        else:
            # No inset, content image is the full image
            image = content_image
            logging.info(f"No border inset applied, full canvas size: {W}x{H}")
        
        logging.info(f"Border inset applied: {inset_pixels}px → Canvas: {W}x{H} (sprite: {SPRITE_BOX['w']}x{SPRITE_BOX['h']}, sections: date={DATE_SECTION['w']}x{DATE_SECTION['h']}, info={INFO_SECTION['w']}x{INFO_SECTION['h']}, desc={DESC_SECTION['w']}x{DESC_SECTION['h']})")
        
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