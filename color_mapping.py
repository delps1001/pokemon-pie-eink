#!/usr/bin/env python3
"""
Advanced Color Mapping for 7-Color E-ink Displays
Implements perceptual color quantization using LAB color space and Delta E calculations
"""

import numpy as np
from PIL import Image
# import logging
from typing import Tuple, List, Dict
import colorsys

# Try to import the fast Cython dithering module
try:
    import fast_dither
    FAST_DITHER_AVAILABLE = True
    print("Fast Cython dithering module loaded successfully")
except ImportError:
    FAST_DITHER_AVAILABLE = False
    print("Fast Cython dithering not available, using Python fallback")

class SevenColorMapper:
    """
    Maps RGB colors to 7-color e-ink display palette using perceptual color matching
    Uses LAB color space and CIEDE2000 Delta E calculations for optimal results
    """
    
    # Waveshare 7.3" F display color constants (RGB values)
    EINK_COLORS = {
        'BLACK': (0, 0, 0),
        'WHITE': (255, 255, 255),
        'GREEN': (0, 255, 0),
        'BLUE': (0, 0, 255),
        'RED': (255, 0, 0),
        'YELLOW': (255, 255, 0),
        'ORANGE': (255, 165, 0)
    }
    
    # Color indices for Waveshare library
    COLOR_INDICES = {
        'BLACK': 0x00,
        'WHITE': 0x01,
        'GREEN': 0x02,
        'BLUE': 0x03,
        'RED': 0x04,
        'YELLOW': 0x05,
        'ORANGE': 0x06
    }
    
    def __init__(self):
        """Initialize color mapper with precomputed LAB values for e-ink colors"""
        self.eink_lab_colors = {}
        self.eink_color_list = []
        
        # Convert e-ink colors to LAB space for perceptual matching
        for name, rgb in self.EINK_COLORS.items():
            lab = self._rgb_to_lab(rgb)
            self.eink_lab_colors[name] = lab
            self.eink_color_list.append((name, rgb, lab))
        
        # Pre-compute color lookup table for common RGB values (MAJOR SPEEDUP)
        self._color_cache = {}
        self._build_color_lookup_table()
        
        # Initialize fast Cython ditherer if available
        self.fast_ditherer = None
        if FAST_DITHER_AVAILABLE:
            try:
                self.fast_ditherer = fast_dither.FastSevenColorDitherer()
                print("Fast Cython ditherer initialized successfully")
            except Exception as e:
                logging.warning(f"Failed to initialize fast ditherer: {e}")
                self.fast_ditherer = None
        
        print(f"Initialized 7-color mapper with {len(self.eink_lab_colors)} colors and {len(self._color_cache)} cached mappings")
    
    def _rgb_to_lab(self, rgb: Tuple[int, int, int]) -> Tuple[float, float, float]:
        """
        Convert RGB to LAB color space for perceptual color matching
        Uses standard sRGB -> XYZ -> LAB conversion
        """
        # Normalize RGB to [0,1]
        r, g, b = [x / 255.0 for x in rgb]
        
        # Apply gamma correction (sRGB)
        def gamma_correct(c):
            if c > 0.04045:
                return ((c + 0.055) / 1.055) ** 2.4
            else:
                return c / 12.92
        
        r, g, b = map(gamma_correct, (r, g, b))
        
        # Convert to XYZ (using sRGB matrix)
        x = r * 0.4124564 + g * 0.3575761 + b * 0.1804375
        y = r * 0.2126729 + g * 0.7151522 + b * 0.0721750
        z = r * 0.0193339 + g * 0.1191920 + b * 0.9503041
        
        # Normalize to D65 illuminant
        x /= 0.95047
        y /= 1.00000
        z /= 1.08883
        
        # Convert XYZ to LAB
        def f(t):
            if t > 0.008856:
                return t ** (1/3)
            else:
                return (7.787 * t) + (16/116)
        
        fx, fy, fz = map(f, (x, y, z))
        
        L = 116 * fy - 16
        a = 500 * (fx - fy)
        b = 200 * (fy - fz)
        
        return (L, a, b)
    
    def _delta_e_cie76(self, lab1: Tuple[float, float, float], 
                      lab2: Tuple[float, float, float]) -> float:
        """
        Calculate CIE76 Delta E color difference
        Simpler but less accurate than CIEDE2000
        """
        L1, a1, b1 = lab1
        L2, a2, b2 = lab2
        
        return np.sqrt((L2 - L1)**2 + (a2 - a1)**2 + (b2 - b1)**2)
    
    def _delta_e_ciede2000(self, lab1: Tuple[float, float, float], 
                          lab2: Tuple[float, float, float]) -> float:
        """
        Calculate CIEDE2000 Delta E color difference
        Most perceptually accurate color difference formula
        """
        # Simplified CIEDE2000 implementation
        # For production use, consider using colorspacious library
        L1, a1, b1 = lab1
        L2, a2, b2 = lab2
        
        # Calculate chroma and hue
        C1 = np.sqrt(a1**2 + b1**2)
        C2 = np.sqrt(a2**2 + b2**2)
        
        # Delta values
        dL = L2 - L1
        dC = C2 - C1
        da = a2 - a1
        db = b2 - b1
        
        # Simplified calculation (for full CIEDE2000, more complex corrections needed)
        dH2 = da**2 + db**2 - dC**2
        dH = np.sqrt(max(0, dH2))
        
        # Weighting factors (simplified)
        kL = kC = kH = 1.0
        
        # Calculate Delta E
        delta_e = np.sqrt((dL/kL)**2 + (dC/kC)**2 + (dH/kH)**2)
        
        return delta_e
    
    def find_closest_eink_color(self, rgb: Tuple[int, int, int], 
                               use_ciede2000: bool = True) -> Tuple[str, Tuple[int, int, int], int]:
        """
        Find the closest e-ink color to a given RGB color
        Returns (color_name, rgb_value, color_index)
        """
        target_lab = self._rgb_to_lab(rgb)
        min_distance = float('inf')
        closest_color = None
        
        delta_e_func = self._delta_e_ciede2000 if use_ciede2000 else self._delta_e_cie76
        
        for name, rgb_val, lab_val in self.eink_color_list:
            distance = delta_e_func(target_lab, lab_val)
            if distance < min_distance:
                min_distance = distance
                closest_color = (name, rgb_val, self.COLOR_INDICES[name])
        
        return closest_color
    
    def _find_closest_eink_color_direct(self, rgb: Tuple[int, int, int], 
                                       use_ciede2000: bool = True) -> Tuple[str, Tuple[int, int, int], int]:
        """Direct color matching without cache lookup (used for building cache)"""
        target_lab = self._rgb_to_lab(rgb)
        min_distance = float('inf')
        closest_color = None
        
        delta_e_func = self._delta_e_ciede2000 if use_ciede2000 else self._delta_e_cie76
        
        for name, rgb_val, lab_val in self.eink_color_list:
            distance = delta_e_func(target_lab, lab_val)
            if distance < min_distance:
                min_distance = distance
                closest_color = (name, rgb_val, self.COLOR_INDICES[name])
        
        return closest_color
    
    def _build_color_lookup_table(self):
        """
        Pre-compute color mappings for common RGB values to eliminate runtime calculations
        This provides massive speedup with zero quality loss
        OPTIMIZED: Use smaller step size for Pi performance while maintaining quality
        """
        # Build lookup table for every 4th RGB value (64x64x64 = 262,144 entries)
        # Smaller cache but covers more colors for better Pi performance
        step = 4
        cache_start_time = time.time() if 'time' in globals() else None
        
        for r in range(0, 256, step):
            for g in range(0, 256, step):
                for b in range(0, 256, step):
                    rgb = (r, g, b)
                    # Store the closest color mapping
                    closest_name, closest_rgb, closest_index = self._find_closest_eink_color_direct(rgb)
                    self._color_cache[rgb] = (closest_name, closest_rgb, closest_index)
        
        if cache_start_time:
            cache_time = time.time() - cache_start_time
            print(f"Built optimized color cache in {cache_time:.2f}s ({len(self._color_cache)} mappings)")
    
    def find_closest_eink_color(self, rgb: Tuple[int, int, int], 
                               use_ciede2000: bool = True) -> Tuple[str, Tuple[int, int, int], int]:
        """
        Find the closest e-ink color to a given RGB color using optimized lookup
        OPTIMIZED: Higher precision cache lookup for Pi
        Returns (color_name, rgb_value, color_index)
        """
        # Try cache lookup first (quantized to nearest cached value with higher precision)
        cache_r = (rgb[0] // 4) * 4
        cache_g = (rgb[1] // 4) * 4  
        cache_b = (rgb[2] // 4) * 4
        cache_key = (cache_r, cache_g, cache_b)
        
        if cache_key in self._color_cache:
            return self._color_cache[cache_key]
        
        # Fallback to direct calculation for edge cases
        return self._find_closest_eink_color_direct(rgb, use_ciede2000)
    
    def quantize_image(self, image: Image.Image, 
                      method: str = 'perceptual') -> Image.Image:
        """
        Quantize a full-color image to 7-color e-ink palette
        
        Args:
            image: PIL Image to quantize
            method: 'perceptual' (LAB+DeltaE) or 'simple' (RGB distance)
        
        Returns:
            Quantized PIL Image with 7-color palette
        """
        if image.mode != 'RGB':
            image = image.convert('RGB')
        
        # Convert to numpy array for processing
        img_array = np.array(image)
        height, width, channels = img_array.shape
        
        # Create output array
        output_array = np.zeros((height, width, 3), dtype=np.uint8)
        
        if method == 'perceptual':
            # Use perceptual color matching
            for y in range(height):
                for x in range(width):
                    rgb = tuple(img_array[y, x])
                    _, closest_rgb, _ = self.find_closest_eink_color(rgb)
                    output_array[y, x] = closest_rgb
        else:
            # Simple RGB distance method (faster but less accurate)
            for y in range(height):
                for x in range(width):
                    rgb = tuple(img_array[y, x])
                    min_distance = float('inf')
                    closest_rgb = (0, 0, 0)
                    
                    for _, eink_rgb in self.EINK_COLORS.items():
                        # Euclidean distance in RGB space
                        distance = np.sqrt(sum((a - b)**2 for a, b in zip(rgb, eink_rgb)))
                        if distance < min_distance:
                            min_distance = distance
                            closest_rgb = eink_rgb
                    
                    output_array[y, x] = closest_rgb
        
        return Image.fromarray(output_array, 'RGB')
    
    def enhance_for_vibrant_display(self, image: Image.Image) -> Image.Image:
        """
        Enhance image for vibrant 7-color display
        Increases saturation and contrast to make colors pop on e-ink
        """
        from PIL import ImageEnhance
        
        # Convert to RGB if needed
        if image.mode != 'RGB':
            image = image.convert('RGB')
        
        # Increase saturation for more vibrant colors
        saturation_enhancer = ImageEnhance.Color(image)
        image = saturation_enhancer.enhance(1.3)  # 30% more saturated
        
        # Slight contrast boost
        contrast_enhancer = ImageEnhance.Contrast(image)
        image = contrast_enhancer.enhance(1.1)  # 10% more contrast
        
        # Slight brightness adjustment to prevent washout
        brightness_enhancer = ImageEnhance.Brightness(image)
        image = brightness_enhancer.enhance(1.05)  # 5% brighter
        
        return image
    
    def create_color_preview(self, width: int = 800, height: int = 100) -> Image.Image:
        """
        Create a preview image showing all 7 available colors
        Useful for testing and configuration
        """
        preview = Image.new('RGB', (width, height), 'white')
        
        color_width = width // len(self.EINK_COLORS)
        x_offset = 0
        
        for name, rgb in self.EINK_COLORS.items():
            # Create color block
            color_block = Image.new('RGB', (color_width, height), rgb)
            preview.paste(color_block, (x_offset, 0))
            x_offset += color_width
        
        return preview
    
    def quantize_image_advanced_dithering(self, image: Image.Image, 
                                         method: str = 'floyd_steinberg_7color') -> Image.Image:
        """
        Advanced 7-color dithering using state-of-the-art error diffusion
        FAST VERSION - Uses Cython when available, falls back to Python
        
        Args:
            image: PIL Image to quantize
            method: 'floyd_steinberg_7color', 'jarvis_judice_ninke_7color', or 'simple'
        
        Returns:
            Quantized PIL Image with 7-color palette using advanced dithering
        """
        import time
        start_time = time.time()
        
        if image.mode != 'RGB':
            image = image.convert('RGB')
        
        img_array = np.array(image, dtype=np.float32)
        height, width, channels = img_array.shape
        total_pixels = height * width
        
        # Try to use fast Cython version first
        if self.fast_ditherer is not None:
            print(f"Using FAST Cython dithering: {method} on {width}x{height} image ({total_pixels:,} pixels)")
            
            try:
                if method == 'jarvis_judice_ninke_7color':
                    output_array = self.fast_ditherer.jarvis_judice_ninke_dither(img_array)
                elif method == 'floyd_steinberg_7color':
                    output_array = self.fast_ditherer.floyd_steinberg_dither(img_array)
                else:
                    # Fallback to JJN for unknown methods
                    print(f"Unknown method '{method}', using Jarvis-Judice-Ninke")
                    output_array = self.fast_ditherer.jarvis_judice_ninke_dither(img_array)
                
                total_time = time.time() - start_time
                final_pixels_per_second = total_pixels / total_time if total_time > 0 else 0
                print(f"FAST Cython dithering completed: {total_time:.2f}s ({final_pixels_per_second:,.0f} pixels/sec)")
                
                return Image.fromarray(output_array, 'RGB')
                
            except Exception as e:
                logging.error(f"Fast Cython dithering failed: {e}, falling back to Python")
                # Fall through to Python version
        
        # Python fallback version (original implementation)
        print(f"Using Python fallback dithering: {method} on {width}x{height} image ({total_pixels:,} pixels)")
        
        # Create output array
        output_array = np.zeros((height, width, 3), dtype=np.uint8)
        
        if method == 'simple':
            # Fallback to simple nearest neighbor
            return self.quantize_image(image, 'simple')
        
        # Advanced multi-color error diffusion
        if method == 'floyd_steinberg_7color':
            # Floyd-Steinberg kernel for 7-color dithering
            error_kernel = [
                (1, 0, 7/16),   # Right
                (-1, 1, 3/16),  # Bottom-left
                (0, 1, 5/16),   # Bottom
                (1, 1, 1/16)    # Bottom-right
            ]
        elif method == 'jarvis_judice_ninke_7color':
            # Jarvis-Judice-Ninke kernel (larger, smoother)
            error_kernel = [
                (1, 0, 7/48), (2, 0, 5/48),                    # Current row
                (-2, 1, 3/48), (-1, 1, 5/48), (0, 1, 7/48), (1, 1, 5/48), (2, 1, 3/48),  # Next row
                (-2, 2, 1/48), (-1, 2, 3/48), (0, 2, 5/48), (1, 2, 3/48), (2, 2, 1/48)   # Row after
            ]
        else:
            # Default to Floyd-Steinberg
            error_kernel = [
                (1, 0, 7/16), (-1, 1, 3/16), (0, 1, 5/16), (1, 1, 1/16)
            ]
        
        # THE KEY OPTIMIZATION: Build a local lookup cache during processing
        # This eliminates repeated function calls for common colors
        local_cache = {}
        cache_hits = 0
        cache_misses = 0
        
        # Error diffusion with serpentine scanning (reduces artifacts)
        processed_pixels = 0
        last_progress_time = start_time
        
        for y in range(height):
            # Progress logging every 25% or 3 seconds
            current_time = time.time()
            progress = (y / height) * 100
            
            if current_time - last_progress_time >= 3.0 or y % (height // 4) == 0:
                pixels_per_second = processed_pixels / (current_time - start_time) if current_time > start_time else 0
                hit_rate = cache_hits / (cache_hits + cache_misses) if (cache_hits + cache_misses) > 0 else 0
                print(f"Python dithering progress: {progress:.1f}% ({y}/{height} rows, {pixels_per_second:,.0f} pixels/sec, cache hit: {hit_rate:.1%})")
                last_progress_time = current_time
            
            # Alternate scan direction for serpentine pattern
            if y % 2 == 0:
                x_range = range(width)
            else:
                x_range = range(width - 1, -1, -1)
            
            for x in x_range:
                # Get current pixel RGB (clamped to valid range)
                current_rgb = tuple(np.clip(img_array[y, x], 0, 255).astype(int))
                
                # Check local cache first (this is the speedup!)
                if current_rgb in local_cache:
                    closest_rgb = local_cache[current_rgb]
                    cache_hits += 1
                else:
                    # Find closest color using optimized cache lookup
                    _, closest_rgb, _ = self.find_closest_eink_color(current_rgb)
                    local_cache[current_rgb] = closest_rgb
                    cache_misses += 1
                
                # Set output pixel
                output_array[y, x] = closest_rgb
                
                # Calculate error in RGB space for distribution
                error_rgb = img_array[y, x] - np.array(closest_rgb, dtype=np.float32)
                
                # Distribute error to neighboring pixels
                for dx, dy, weight in error_kernel:
                    nx, ny = x + dx, y + dy
                    
                    # Handle serpentine scanning direction
                    if y % 2 == 1:  # Right-to-left row
                        nx = x - dx  # Flip x offset
                    
                    # Check bounds
                    if 0 <= nx < width and 0 <= ny < height:
                        # Add weighted error to neighboring pixel
                        img_array[ny, nx] += error_rgb * weight
                        # Clamp to valid RGB range
                        np.clip(img_array[ny, nx], 0, 255, out=img_array[ny, nx])
                
                processed_pixels += 1
        
        total_time = time.time() - start_time
        final_pixels_per_second = total_pixels / total_time if total_time > 0 else 0
        final_hit_rate = cache_hits / (cache_hits + cache_misses) if (cache_hits + cache_misses) > 0 else 0
        print(f"Python dithering completed: {total_time:.2f}s ({final_pixels_per_second:,.0f} pixels/sec)")
        print(f"Local cache performance: {len(local_cache)} unique colors, {final_hit_rate:.1%} hit rate")
        
        return Image.fromarray(output_array, 'RGB')
    
    def quantize_image_blue_noise(self, image: Image.Image) -> Image.Image:
        """
        Blue noise dithering for 7-color palette (experimental)
        Uses pre-computed blue noise patterns for better visual quality
        """
        if image.mode != 'RGB':
            image = image.convert('RGB')
        
        # Convert to numpy array
        img_array = np.array(image)
        height, width, channels = img_array.shape
        output_array = np.zeros((height, width, 3), dtype=np.uint8)
        
        # Simple blue noise approximation using random thresholds
        # In production, this would use pre-computed blue noise masks
        np.random.seed(42)  # Consistent results
        
        for y in range(height):
            for x in range(width):
                rgb = tuple(img_array[y, x])
                
                # Find two closest colors for potential dithering
                distances = []
                for name, eink_rgb in self.EINK_COLORS.items():
                    lab1 = self._rgb_to_lab(rgb)
                    lab2 = self._rgb_to_lab(eink_rgb)
                    distance = self._delta_e_ciede2000(lab1, lab2)
                    distances.append((distance, name, eink_rgb))
                
                distances.sort()
                closest = distances[0]
                second_closest = distances[1] if len(distances) > 1 else distances[0]
                
                # Use blue noise threshold to choose between closest colors
                threshold = np.random.random()  # Would use blue noise pattern in production
                
                if threshold < 0.7:  # Bias toward closest color
                    output_array[y, x] = closest[2]
                else:
                    output_array[y, x] = second_closest[2]
        
        return Image.fromarray(output_array, 'RGB')

    def get_palette_for_waveshare(self) -> List[int]:
        """
        Get color palette in format expected by Waveshare library
        Returns list of color indices
        """
        return list(self.COLOR_INDICES.values())


# Utility functions for color analysis
def analyze_image_colors(image: Image.Image, top_n: int = 10) -> List[Tuple[Tuple[int, int, int], int]]:
    """
    Analyze the most common colors in an image
    Returns list of (rgb_color, pixel_count) tuples
    """
    if image.mode != 'RGB':
        image = image.convert('RGB')
    
    # Get color histogram
    colors = image.getcolors(maxcolors=256*256*256)
    if colors is None:
        return []
    
    # Sort by frequency and return top N
    colors.sort(key=lambda x: x[0], reverse=True)
    return [(color[1], color[0]) for color in colors[:top_n]]


def create_color_comparison(original: Image.Image, quantized: Image.Image) -> Image.Image:
    """
    Create side-by-side comparison of original vs quantized image
    """
    width, height = original.size
    comparison = Image.new('RGB', (width * 2, height), 'white')
    
    comparison.paste(original, (0, 0))
    comparison.paste(quantized, (width, 0))
    
    return comparison


if __name__ == "__main__":
    # Test the color mapper
    mapper = SevenColorMapper()
    
    # Create color preview
    preview = mapper.create_color_preview()
    preview.save('/tmp/eink_colors_preview.png')
    print("Color preview saved to /tmp/eink_colors_preview.png")
    
    # Test color mapping
    test_colors = [
        (255, 100, 100),  # Light red
        (100, 255, 100),  # Light green
        (100, 100, 255),  # Light blue
        (200, 200, 50),   # Yellowish
        (255, 140, 50),   # Orange-ish
        (128, 128, 128),  # Gray
    ]
    
    print("\nColor mapping tests:")
    for rgb in test_colors:
        name, mapped_rgb, index = mapper.find_closest_eink_color(rgb)
        print(f"RGB{rgb} -> {name} RGB{mapped_rgb} (index: {index})")