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

    def add_generation_authentic_type_icons(self, image, pokemon, area_x, area_y, area_width):
        """Add authentic generation-specific Pokemon type icons to the display"""
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
            for icon_path in type_icon_paths:
                try:
                    if icon_path.exists():
                        type_icon = Image.open(icon_path)
                        
                        # Scale type icons for e-ink visibility while preserving authenticity
                        # Type icons are typically 32x14 or similar, scale up for e-ink readability
                        target_height = 32  # Good balance between authenticity and visibility
                        scale_factor = target_height / type_icon.height
                        new_width = int(type_icon.width * scale_factor)
                        new_height = target_height
                        
                        # Use nearest neighbor for pixel-perfect authentic scaling
                        type_icon = type_icon.resize((new_width, new_height), Image.Resampling.NEAREST)
                        
                        # Apply specialized e-ink processing for type icons
                        type_icon = self.enhance_sprite_for_eink(type_icon)
                        
                        type_icons.append(type_icon)
                        logging.info(f"Loaded authentic Gen-{pokemon_generation} type icon: {icon_path.name} -> {new_width}x{new_height}")
                    
                except Exception as e:
                    logging.warning(f"Failed to load type icon {icon_path}: {e}")
            
            if not type_icons:
                return False
            
            # Calculate positioning for type icons (left-aligned in text column)
            start_x = area_x  # Left-align to start of text column
            
            # Paste type icons onto the main image
            current_x = start_x
            for type_icon in type_icons:
                image.paste(type_icon, (current_x, area_y))
                current_x += type_icon.width + 8  # 8px gap between icons
            
            logging.info(f"Added {len(type_icons)} authentic Gen-{pokemon_generation} type icons for {pokemon['name']}: {', '.join(pokemon_types)}")
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

    def create_display_image(self):
        """Create pixel-perfect e-ink display image following exact specifications"""
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
        else:
            BLACK = 0
            WHITE = 255
        
        # Get current Pokemon
        pokemon = self.get_current_pokemon()
        
        # Regions (proportional to effective canvas) - maintain layout proportions
        # Original layout: SPRITE_BOX was 448x448 on 880x528 canvas (51% width, 85% height)
        sprite_box_width = int(W * 0.51)  # Maintain proportion
        sprite_box_height = int(H * 0.85)  # Maintain proportion
        
        # Ensure sprite box is square (use smaller dimension)
        sprite_box_size = min(sprite_box_width, sprite_box_height)
        
        SPRITE_BOX = {'x': M, 'y': M, 'w': sprite_box_size, 'h': sprite_box_size}
        RIGHT = {
            'x': M + SPRITE_BOX['w'] + G,
            'y': M,
            'w': W - (M + SPRITE_BOX['w'] + G) - M,
            'h': H - 2*M
        }
        
        # SPRITE - Load and process Pokemon sprite
        sprite = None
        sprite_path = pokemon.get('local_sprite')
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
        
        # TEXT CONTENT - All coordinates relative to RIGHT column
        y = RIGHT['y']  # Start at top of right column
        
        # Get text content
        current_date = datetime.now()
        weekday = current_date.strftime("%A")
        full_date = current_date.strftime("%B %d, %Y")
        pokemon_name = pokemon.get('name', f"Pokemon #{pokemon['id']:03d}")
        pokemon_id_text = f"#{pokemon['id']:03d}"
        
        # DATE BLOCK (top-aligned, left-aligned)
        if self.font_date_weekday:
            # Weekday at (x=0, y=0) relative to right column
            weekday_bbox = draw.textbbox((0, 0), weekday, font=self.font_date_weekday)
            weekday_height = weekday_bbox[3] - weekday_bbox[1]
            draw.text((RIGHT['x'], y), weekday, font=self.font_date_weekday, fill=BLACK)
            y += weekday_height + 8  # 8px below weekday baseline
        
        if self.font_date_full:
            # Full date on next line
            date_bbox = draw.textbbox((0, 0), full_date, font=self.font_date_full)
            date_height = date_bbox[3] - date_bbox[1]
            draw.text((RIGHT['x'], y), full_date, font=self.font_date_full, fill=BLACK)
            y += date_height + 16  # 16px after date block
        
        # DEX NUMBER + NAME (left-aligned)
        if self.font_dex_number:
            # Dex number on one line
            dex_bbox = draw.textbbox((0, 0), pokemon_id_text, font=self.font_dex_number)
            dex_height = dex_bbox[3] - dex_bbox[1]
            draw.text((RIGHT['x'], y), pokemon_id_text, font=self.font_dex_number, fill=BLACK)
            y += dex_height + 8  # 8px below dex line
        
        # Pokemon name with autosizing
        if self.font_name_primary and pokemon_name:
            name_font = self.font_name_primary
            name_size = 52  # Start at target size
            
            # Autosize: start at 52px, decrement by 2px until fits or reaches 34px
            while name_size >= 34:
                try:
                    test_font = ImageFont.truetype(self.font_name_primary.path, name_size)
                    name_bbox = draw.textbbox((0, 0), pokemon_name, font=test_font)
                    measured_width = name_bbox[2] - name_bbox[0]
                    
                    if measured_width <= RIGHT['w']:
                        name_font = test_font
                        break
                    
                    name_size -= 2
                except:
                    # Fallback if font path not available
                    name_bbox = draw.textbbox((0, 0), pokemon_name, font=name_font)
                    measured_width = name_bbox[2] - name_bbox[0]
                    if measured_width <= RIGHT['w']:
                        break
                    name_font = self.font_date_full  # Fallback
                    break
            
            # Draw name
            name_bbox = draw.textbbox((0, 0), pokemon_name, font=name_font)
            name_height = name_bbox[3] - name_bbox[1]
            draw.text((RIGHT['x'], y), pokemon_name, font=name_font, fill=BLACK)
            y += name_height + 12  # 12px after name
            
            logging.info(f"Pokemon name '{pokemon_name}' rendered at {name_size}px")
        
        # AUTHENTIC TYPE ICONS (generation-specific sprites)
        pokemon_types = pokemon.get('types', [])
        pokemon_generation = pokemon.get('generation', 1)
        if pokemon_types:
            type_icons_added = self.add_generation_authentic_type_icons(
                content_image, pokemon, RIGHT['x'], y, RIGHT['w']
            )
            if type_icons_added:
                y += 32 + 16  # Height of type icons + gap
                logging.info(f"Added authentic Gen-{pokemon_generation} type icons for {pokemon['name']}: {', '.join(pokemon_types)}")
            else:
                # Fallback to text if icons not available (left-aligned)
                logging.warning(f"Using text fallback for types: {', '.join(pokemon_types)}")
                type_text = " / ".join(t.upper() for t in pokemon_types)
                if self.font_type_pills:
                    # Left-align the text fallback
                    draw.text((RIGHT['x'], y), type_text, font=self.font_type_pills, fill=BLACK)
                    y += 24 + 16  # Text height + gap
        
        # FLAVOR TEXT (left-aligned, paragraph)
        pokemon_id = pokemon.get('id')
        if pokemon_id and self.font_flavor_text:
            pokedex_text = get_pokedex_description(pokemon_id)
            if pokedex_text:
                # Calculate available space
                available_height = H - y - M  # Remaining space to bottom margin
                
                # Start with target size, auto-shrink if needed
                text_size = 22
                while text_size >= 18:
                    try:
                        if text_size != 22:
                            test_font = ImageFont.truetype(self.font_flavor_text.path, text_size)
                        else:
                            test_font = self.font_flavor_text
                    except:
                        test_font = self.font_flavor_text
                    
                    # Wrap text and count lines
                    wrapped_lines = self.wrap_text(pokedex_text, test_font, RIGHT['w'])
                    line_height = 28
                    total_text_height = len(wrapped_lines) * line_height
                    
                    # Check if it fits (max 6 lines as per spec)
                    if len(wrapped_lines) <= 6 and total_text_height <= available_height:
                        break
                    
                    text_size -= 1
                
                # Draw flavor text (limit to 6 lines max)
                lines_to_draw = wrapped_lines[:6]
                if len(wrapped_lines) > 6:
                    # Add ellipsis to last line if truncated
                    if lines_to_draw:
                        lines_to_draw[-1] = lines_to_draw[-1].rstrip() + "…"
                
                for line in lines_to_draw:
                    if line.strip():
                        draw.text((RIGHT['x'], y), line, font=test_font, fill=BLACK)
                        y += line_height
                
                logging.info(f"Flavor text rendered at {text_size}px, {len(lines_to_draw)} lines")
        
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
        
        logging.info(f"Border inset applied: {inset_pixels}px → Canvas: {W}x{H} (sprite: {SPRITE_BOX['w']}x{SPRITE_BOX['h']}, text: {RIGHT['w']}x{RIGHT['h']})")
        
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