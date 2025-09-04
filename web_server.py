#!/usr/bin/env python3
"""
Web server for remote control of Pokemon E-ink Calendar
Provides REST API and WebSocket endpoints for configuration and control
"""

import asyncio
import json
import logging
import os
import threading
import socket
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import asdict

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import traceback

try:
    from zeroconf import ServiceInfo, Zeroconf
    ZEROCONF_AVAILABLE = True
except ImportError:
    logging.warning("zeroconf not available. mDNS service broadcasting disabled.")
    ZEROCONF_AVAILABLE = False

from pokemon_data_with_types import get_pokemon_info


class PokemonInfo(BaseModel):
    id: int
    name: str
    types: List[str]
    generation: int
    local_sprite: Optional[str] = None


class SystemStatus(BaseModel):
    current_pokemon: PokemonInfo
    demo_mode: bool
    display_width: int
    display_height: int
    display_type: str = "7in5_HD"
    color_mode: str = "monochrome"
    total_pokemon_count: int
    last_update: Optional[str] = None
    epd_available: bool = False


class ConfigUpdate(BaseModel):
    display: Optional[Dict[str, Any]] = None
    pokemon: Optional[Dict[str, Any]] = None
    demo: Optional[Dict[str, Any]] = None
    image_processing: Optional[Dict[str, Any]] = None
    cache: Optional[Dict[str, Any]] = None
    logging: Optional[Dict[str, Any]] = None


class PokemonScheduleEntry(BaseModel):
    date: str
    day_name: str
    pokemon: PokemonInfo
    is_today: bool = False


class WebSocketManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logging.info(f"WebSocket client connected. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logging.info(f"WebSocket client disconnected. Total: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        if not self.active_connections:
            return
        
        message_str = json.dumps(message)
        disconnected = []
        
        for connection in self.active_connections:
            try:
                await connection.send_text(message_str)
            except Exception as e:
                logging.warning(f"Failed to send WebSocket message: {e}")
                disconnected.append(connection)
        
        # Clean up disconnected clients
        for connection in disconnected:
            self.disconnect(connection)


class PokemonWebServer:
    def __init__(self, pokemon_calendar=None, host="0.0.0.0", port=8000):
        self.pokemon_calendar = pokemon_calendar
        self.host = host
        self.port = port
        self.app = FastAPI(title="Pokemon E-ink Calendar Control", version="1.0.0")
        self.websocket_manager = WebSocketManager()
        self.server = None
        self.server_thread = None
        self._pending_messages = []
        self._message_check_running = False
        
        # mDNS service broadcasting
        self.zeroconf = None
        self.service_info = None

        self._setup_app()
        self._setup_routes()

    def _get_local_ip(self):
        """Get the local IP address for mDNS service registration"""
        try:
            # Connect to a dummy address to find local IP
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"

    def _setup_mdns_service(self):
        """Set up mDNS service broadcasting for Pokemon Calendar discovery"""
        if not ZEROCONF_AVAILABLE:
            logging.info("mDNS service broadcasting not available (zeroconf not installed)")
            return
        
        try:
            local_ip = self._get_local_ip()
            logging.info(f"mDNS using local IP {local_ip} for registration")
            
            # Create unique service names to avoid conflicts
            import time
            import random
            timestamp = int(time.time())
            random_suffix = random.randint(1000, 9999)
            unique_id = f"{timestamp}-{random_suffix}"
            
            service_name = f"pokecal-{unique_id}._pokecal._tcp.local."
            service_type = "_pokecal._tcp.local."
            
            # Get current Pokemon info for service properties
            properties = {
                'version': '1.0.0',
                'api_version': '1',
                'service': 'pokecal',
                'path': '/api/status',
                'instance_id': unique_id
            }
            
            # Add current Pokemon info if available
            if self.pokemon_calendar:
                try:
                    current_pokemon = self.pokemon_calendar.get_current_pokemon()
                    pokemon_info = PokemonInfo(
                        id=current_pokemon['id'],
                        name=current_pokemon['name'],
                        types=current_pokemon.get('types', []),
                        generation=current_pokemon.get('generation', 1),
                        local_sprite=current_pokemon.get('local_sprite')
                    )
                    
                    config = self.pokemon_calendar.config
                    demo_mode = getattr(self.pokemon_calendar, 'demo_mode', False)
                    display_config = config.get('display', {})
                    properties.update({
                        'current_pokemon': pokemon_info.name,
                        'pokemon_id': str(pokemon_info.id),
                        'demo_mode': str(demo_mode),
                        'display_type': display_config.get('type', 'unknown'),
                        'color_mode': display_config.get('color_mode', 'monochrome')
                    })
                except Exception as e:
                    logging.warning(f"Could not get Pokemon info for mDNS: {e}")
            
            # Convert properties to bytes
            properties_bytes = {k: v.encode('utf-8') for k, v in properties.items()}
            
            self.service_info = ServiceInfo(
                service_type,
                service_name,
                addresses=[socket.inet_aton(local_ip)],
                port=self.port,
                properties=properties_bytes,
                server=f"pokecal-{local_ip.replace('.', '-')}.local."
            )
            
            self.zeroconf = Zeroconf()
            
            # Register service with retry logic for conflicts
            try:
                self.zeroconf.register_service(self.service_info)
                logging.info(f"mDNS service registered: {service_name} on {local_ip}:{self.port}")
            except Exception as reg_error:
                if "NonUniqueNameException" in str(reg_error):
                    logging.warning(f"mDNS service name conflict for {service_name}, trying alternative name")
                    # Try with additional random suffix
                    alt_suffix = random.randint(10000, 99999)
                    alt_service_name = f"pokecal-{unique_id}-{alt_suffix}._pokecal._tcp.local."
                    self.service_info = ServiceInfo(
                        service_type,
                        alt_service_name,
                        addresses=[socket.inet_aton(local_ip)],
                        port=self.port,
                        properties=properties_bytes,
                        server=f"pokecal-{local_ip.replace('.', '-')}.local."
                    )
                    try:
                        self.zeroconf.register_service(self.service_info)
                        logging.info(f"mDNS service registered with alternative name: {alt_service_name} on {local_ip}:{self.port}")
                    except Exception as alt_error:
                        logging.warning(f"Failed to register alternative mDNS service: {alt_error}")
                        raise alt_error
                else:
                    raise reg_error
            
        except Exception as e:
            logging.error(f"Failed to set up mDNS service: {e}")
            traceback.print_exc()
            self.zeroconf = None
            self.service_info = None

    def _cleanup_mdns_service(self):
        """Clean up mDNS service registration"""
        if self.zeroconf and self.service_info:
            try:
                self.zeroconf.unregister_service(self.service_info)
                self.zeroconf.close()
                logging.info("mDNS service unregistered")
            except Exception as e:
                logging.error(f"Error cleaning up mDNS service: {e}")
            finally:
                self.zeroconf = None
                self.service_info = None

    def _setup_app(self):
        # Add CORS middleware
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # Serve static files (will create the web interface later)
        static_dir = Path(__file__).parent / "static"
        if static_dir.exists():
            self.app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    async def _start_message_checker(self):
        """Start background task to check for pending messages"""
        if self._message_check_running:
            return
        
        self._message_check_running = True
        logging.info("Starting WebSocket message checker")
        
        while self._message_check_running:
            try:
                if self._pending_messages:
                    messages_to_send = self._pending_messages.copy()
                    self._pending_messages.clear()
                    
                    for message in messages_to_send:
                        logging.info(f"Broadcasting pending message: {message['type']}")
                        await self.websocket_manager.broadcast(message)
                
                await asyncio.sleep(0.5)  # Check every 500ms
            except Exception as e:
                logging.error(f"Error in message checker: {e}")
                await asyncio.sleep(1)

    def _setup_routes(self):
        @self.app.get("/", response_class=HTMLResponse)
        async def root():
            return await self._serve_web_interface()

        @self.app.get("/api/status", response_model=SystemStatus)
        async def get_status():
            if not self.pokemon_calendar:
                raise HTTPException(status_code=503, detail="Pokemon calendar not available")
            
            current_pokemon = self.pokemon_calendar.get_current_pokemon()
            pokemon_info = PokemonInfo(
                id=current_pokemon['id'],
                name=current_pokemon['name'],
                types=current_pokemon.get('types', []),
                generation=current_pokemon.get('generation', 1),
                local_sprite=current_pokemon.get('local_sprite')
            )
            
            return SystemStatus(
                current_pokemon=pokemon_info,
                demo_mode=self.pokemon_calendar.demo_mode,
                display_width=self.pokemon_calendar.display_width,
                display_height=self.pokemon_calendar.display_height,
                display_type=getattr(self.pokemon_calendar, 'display_type', '7in5_HD'),
                color_mode=getattr(self.pokemon_calendar, 'color_mode', 'monochrome'),
                total_pokemon_count=len(self.pokemon_calendar.pokemon_data),
                last_update=datetime.now().isoformat(),
                epd_available=self.pokemon_calendar.epd is not None
            )

        @self.app.get("/api/config")
        async def get_config():
            if not self.pokemon_calendar:
                raise HTTPException(status_code=503, detail="Pokemon calendar not available")
            return self.pokemon_calendar.config

        @self.app.post("/api/config")
        async def update_config(config_update: ConfigUpdate):
            if not self.pokemon_calendar:
                raise HTTPException(status_code=503, detail="Pokemon calendar not available")
            
            # Update configuration
            updated_sections = []
            if config_update.display:
                old_display_type = self.pokemon_calendar.config.get('display', {}).get('type', '7in5_HD')
                old_color_mode = self.pokemon_calendar.config.get('display', {}).get('color_mode', 'monochrome')
                
                self.pokemon_calendar.config.setdefault('display', {}).update(config_update.display)
                
                # Check if display type changed - requires reinitialization
                new_display_type = self.pokemon_calendar.config['display'].get('type', '7in5_HD')
                new_color_mode = self.pokemon_calendar.config['display'].get('color_mode', 'monochrome')
                
                if old_display_type != new_display_type or old_color_mode != new_color_mode:
                    logging.info(f"Display type changed from {old_display_type} to {new_display_type}")
                    
                    # Update calendar display configuration
                    self.pokemon_calendar.display_type = new_display_type
                    self.pokemon_calendar.color_mode = new_color_mode
                    
                    # Auto-configure dimensions and color mode based on display type
                    if new_display_type == '7in3e':
                        self.pokemon_calendar.display_width = 800
                        self.pokemon_calendar.display_height = 480
                        self.pokemon_calendar.color_mode = '7color'
                        # Initialize color mapper if needed
                        if not self.pokemon_calendar.color_mapper:
                            from color_mapping import SevenColorMapper
                            self.pokemon_calendar.color_mapper = SevenColorMapper()
                            logging.info("Initialized 7-color mapper for display type change")
                    elif new_display_type == '7in5_HD':
                        display_config = self.pokemon_calendar.config['display']
                        self.pokemon_calendar.display_width = display_config.get('width', 880)
                        self.pokemon_calendar.display_height = display_config.get('height', 528)
                        self.pokemon_calendar.color_mode = 'monochrome'
                        self.pokemon_calendar.color_mapper = None
                    
                    # Reinitialize display hardware (if available)
                    try:
                        if self.pokemon_calendar.epd:
                            # Clean up old display
                            if hasattr(self.pokemon_calendar.epd, 'sleep'):
                                self.pokemon_calendar.epd.sleep()
                        
                        # Initialize new display
                        from waveshare_epd import epd7in5_HD, epd7in3e
                        self.pokemon_calendar.epd = None
                        self.pokemon_calendar.epd_type = None
                        
                        if new_display_type == '7in5_HD' and epd7in5_HD:
                            self.pokemon_calendar.epd = epd7in5_HD.EPD()
                            self.pokemon_calendar.epd.init()
                            self.pokemon_calendar.epd.Clear()
                            self.pokemon_calendar.epd_type = '7in5_HD'
                            logging.info("Reinitialized 7.5\" HD display")
                        elif new_display_type == '7in3e' and epd7in3e:
                            self.pokemon_calendar.epd = epd7in3e.EPD()
                            self.pokemon_calendar.epd.init()
                            self.pokemon_calendar.epd.Clear()
                            self.pokemon_calendar.epd_type = '7in3e'
                            logging.info("Reinitialized 7.3\" 7-color display")
                        
                    except Exception as e:
                        logging.warning(f"Could not reinitialize display hardware: {e}")
                        self.pokemon_calendar.epd = None
                
                updated_sections.append('display')
            
            if config_update.pokemon:
                self.pokemon_calendar.config.setdefault('pokemon', {}).update(config_update.pokemon)
                # Update calendar properties from new config
                pokemon_config = self.pokemon_calendar.config['pokemon']
                self.pokemon_calendar.start_pokemon_id = pokemon_config.get('start_pokemon_id', 1)
                self.pokemon_calendar.start_date = datetime.strptime(
                    pokemon_config.get('start_date', '2024-01-01'), '%Y-%m-%d'
                )
                self.pokemon_calendar.cycle_all_pokemon = pokemon_config.get('cycle_all_pokemon', True)
                self.pokemon_calendar.custom_pokemon_list = pokemon_config.get('custom_pokemon_list', [])
                updated_sections.append('pokemon')
            
            if config_update.demo:
                self.pokemon_calendar.config.setdefault('demo', {}).update(config_update.demo)
                # Update demo mode if changed
                new_demo_mode = config_update.demo.get('enabled', self.pokemon_calendar.demo_mode)
                if new_demo_mode != self.pokemon_calendar.demo_mode:
                    self.pokemon_calendar.demo_mode = new_demo_mode
                updated_sections.append('demo')
            
            if config_update.image_processing:
                self.pokemon_calendar.config.setdefault('image_processing', {}).update(config_update.image_processing)
                updated_sections.append('image_processing')
            
            if config_update.cache:
                self.pokemon_calendar.config.setdefault('cache', {}).update(config_update.cache)
                updated_sections.append('cache')
            
            if config_update.logging:
                self.pokemon_calendar.config.setdefault('logging', {}).update(config_update.logging)
                updated_sections.append('logging')
            
            # Save updated configuration to file
            try:
                with open(self.pokemon_calendar.config_file, 'w') as f:
                    json.dump(self.pokemon_calendar.config, f, indent=2, default=str)
                
                # Broadcast configuration update
                await self.websocket_manager.broadcast({
                    "type": "config_updated",
                    "sections": updated_sections,
                    "timestamp": datetime.now().isoformat()
                })
                
                return {"success": True, "updated_sections": updated_sections}
                
            except Exception as e:
                logging.error(f"Failed to save configuration: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to save configuration: {str(e)}")

        @self.app.post("/api/update-display")
        async def update_display():
            if not self.pokemon_calendar:
                raise HTTPException(status_code=503, detail="Pokemon calendar not available")
            
            try:
                self.pokemon_calendar.update_display()
                
                # Broadcast display update
                await self.websocket_manager.broadcast({
                    "type": "display_updated",
                    "data": {
                        "current_pokemon": self.pokemon_calendar.get_current_pokemon(),
                        "demo_mode": self.pokemon_calendar.demo_mode,
                        "timestamp": datetime.now().isoformat()
                    }
                })
                
                return {"success": True, "message": "Display updated successfully"}
            except Exception as e:
                logging.error(f"Failed to update display: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to update display: {str(e)}")

        @self.app.post("/api/demo-mode/{enabled}")
        async def set_demo_mode(enabled: bool):
            if not self.pokemon_calendar:
                raise HTTPException(status_code=503, detail="Pokemon calendar not available")
            
            # Use the new set_demo_mode method to handle transitions properly
            old_mode = self.pokemon_calendar.set_demo_mode(enabled)
            
            # Update config file
            self.pokemon_calendar.config.setdefault('demo', {})['enabled'] = enabled
            try:
                with open(self.pokemon_calendar.config_file, 'w') as f:
                    json.dump(self.pokemon_calendar.config, f, indent=2, default=str)
            except Exception as e:
                logging.warning(f"Failed to save demo mode to config: {e}")
            
            # Broadcast mode change with fresh Pokemon data
            current_pokemon = self.pokemon_calendar.get_current_pokemon()
            await self.websocket_manager.broadcast({
                "type": "demo_mode_changed",
                "enabled": enabled,
                "previous": old_mode,
                "data": {
                    "current_pokemon": current_pokemon,
                    "demo_mode": enabled,
                    "current_pokemon_index": getattr(self.pokemon_calendar, 'current_pokemon_index', 0),
                    "timestamp": datetime.now().isoformat()
                }
            })
            
            return {"success": True, "demo_mode": enabled, "previous": old_mode}

        @self.app.get("/api/pokemon", response_model=List[PokemonInfo])
        async def list_pokemon(skip: int = 0, limit: int = 100, search: str = None):
            if not self.pokemon_calendar:
                raise HTTPException(status_code=503, detail="Pokemon calendar not available")
            
            pokemon_list = self.pokemon_calendar.pokemon_data[skip:skip + limit]
            
            # Filter by search term if provided
            if search:
                search_lower = search.lower()
                pokemon_list = [
                    p for p in pokemon_list 
                    if search_lower in p['name'].lower() or 
                       search_lower in str(p['id']) or
                       any(search_lower in t.lower() for t in p.get('types', []))
                ]
            
            return [
                PokemonInfo(
                    id=p['id'],
                    name=p['name'],
                    types=p.get('types', []),
                    generation=p.get('generation', 1),
                    local_sprite=p.get('local_sprite')
                ) for p in pokemon_list
            ]

        @self.app.get("/api/pokemon/{pokemon_id}", response_model=PokemonInfo)
        async def get_pokemon(pokemon_id: int):
            if not self.pokemon_calendar:
                raise HTTPException(status_code=503, detail="Pokemon calendar not available")
            
            pokemon = self.pokemon_calendar.get_pokemon_by_id(pokemon_id)
            if not pokemon:
                raise HTTPException(status_code=404, detail=f"Pokemon {pokemon_id} not found")
            
            return PokemonInfo(
                id=pokemon['id'],
                name=pokemon['name'],
                types=pokemon.get('types', []),
                generation=pokemon.get('generation', 1),
                local_sprite=pokemon.get('local_sprite')
            )

        @self.app.post("/api/pokemon/{pokemon_id}/preview")
        async def preview_pokemon(pokemon_id: int):
            if not self.pokemon_calendar:
                raise HTTPException(status_code=503, detail="Pokemon calendar not available")
            
            pokemon = self.pokemon_calendar.get_pokemon_by_id(pokemon_id)
            if not pokemon:
                raise HTTPException(status_code=404, detail=f"Pokemon {pokemon_id} not found")
            
            try:
                # Temporarily override current Pokemon for preview
                original_method = self.pokemon_calendar.get_current_pokemon
                self.pokemon_calendar.get_current_pokemon = lambda: pokemon
                
                # Generate preview image
                image = self.pokemon_calendar.create_display_image()
                preview_path = Path(self.pokemon_calendar.cache_dir) / f"preview_{pokemon_id}.png"
                image.save(preview_path)
                
                # Restore original method
                self.pokemon_calendar.get_current_pokemon = original_method
                
                # Broadcast preview generation
                await self.websocket_manager.broadcast({
                    "type": "preview_generated",
                    "pokemon_id": pokemon_id,
                    "pokemon": pokemon,
                    "preview_path": str(preview_path),
                    "timestamp": datetime.now().isoformat()
                })
                
                return {
                    "success": True,
                    "pokemon": pokemon,
                    "preview_path": str(preview_path),
                    "preview_url": f"/api/preview/{pokemon_id}"
                }
            except Exception as e:
                logging.error(f"Failed to generate preview for Pokemon {pokemon_id}: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to generate preview: {str(e)}")

        @self.app.get("/api/current-display")
        async def get_current_display():
            """Serve the current display image"""
            if not self.pokemon_calendar:
                raise HTTPException(status_code=503, detail="Pokemon calendar not available")
            
            # Determine the correct display file based on display mode
            color_mode = getattr(self.pokemon_calendar, 'color_mode', 'monochrome')
            if color_mode == '7color':
                display_filename = "current_display_7color.png"
            else:
                display_filename = "current_display.png"
            
            display_path = Path(self.pokemon_calendar.cache_dir) / display_filename
            
            if not display_path.exists():
                # Generate current display if it doesn't exist
                try:
                    logging.info(f"Preview image not found at {display_path}, generating new one")
                    image = self.pokemon_calendar.create_display_image()
                    image.save(display_path)
                    # Also save to alternate path for backward compatibility
                    if color_mode == '7color':
                        alt_path = self.pokemon_calendar.cache_dir / "current_display.png"
                    else:
                        alt_path = self.pokemon_calendar.cache_dir / "current_display_7color.png"
                except Exception as e:
                    logging.error(f"Failed to generate current display: {e}")
                    raise HTTPException(status_code=500, detail="Failed to generate display image")
            
            # Return with cache-busting headers
            from fastapi.responses import FileResponse
            response = FileResponse(
                display_path, 
                media_type="image/png",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                    "ETag": f'"{datetime.now().timestamp()}"'
                }
            )
            return response

        @self.app.post("/api/refresh-display-preview")
        async def refresh_display_preview():
            """Force refresh of the display preview image"""
            if not self.pokemon_calendar:
                raise HTTPException(status_code=503, detail="Pokemon calendar not available")
            
            try:
                # Generate fresh display image
                image = self.pokemon_calendar.create_display_image()
                
                # Save to appropriate file based on color mode
                color_mode = getattr(self.pokemon_calendar, 'color_mode', 'monochrome')
                if color_mode == '7color':
                    display_path = Path(self.pokemon_calendar.cache_dir) / "current_display_7color.png"
                else:
                    display_path = Path(self.pokemon_calendar.cache_dir) / "current_display.png"
                
                image.save(display_path)
                logging.info(f"Display preview refreshed and saved to {display_path}")
                
                # Broadcast preview update
                await self.websocket_manager.broadcast({
                    "type": "display_preview_updated",
                    "timestamp": datetime.now().isoformat()
                })
                
                return {"success": True, "message": "Display preview refreshed"}
            except Exception as e:
                logging.error(f"Failed to refresh display preview: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to refresh preview: {str(e)}")

        @self.app.get("/api/preview/{pokemon_id}")
        async def get_preview_image(pokemon_id: int):
            preview_path = Path(self.pokemon_calendar.cache_dir) / f"preview_{pokemon_id}.png"
            if not preview_path.exists():
                raise HTTPException(status_code=404, detail="Preview not found")
            return FileResponse(preview_path, media_type="image/png")

        @self.app.post("/api/set-start-date")
        async def set_start_date(request: Request):
            """Update the calendar start date"""
            if not self.pokemon_calendar:
                raise HTTPException(status_code=503, detail="Pokemon calendar not available")
            
            # Parse JSON with error handling
            try:
                body = await request.json()
            except Exception as e:
                raise HTTPException(status_code=422, detail="Invalid JSON format")
            
            new_start_date = body.get('start_date')
            start_pokemon_id = body.get('start_pokemon_id', 1)
            
            if not new_start_date:
                raise HTTPException(status_code=400, detail="start_date is required")
            
            # Validate date format
            try:
                parsed_date = datetime.strptime(new_start_date, '%Y-%m-%d')
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
            
            try:
                # Update calendar configuration
                self.pokemon_calendar.config.setdefault('pokemon', {})
                self.pokemon_calendar.config['pokemon']['start_date'] = new_start_date
                self.pokemon_calendar.config['pokemon']['start_pokemon_id'] = start_pokemon_id
                
                # Update calendar properties
                self.pokemon_calendar.start_date = parsed_date
                self.pokemon_calendar.start_pokemon_id = start_pokemon_id
                
                # Save configuration
                with open(self.pokemon_calendar.config_file, 'w') as f:
                    json.dump(self.pokemon_calendar.config, f, indent=2, default=str)
                
                # Update display to reflect new date calculation
                self.pokemon_calendar.update_display()
                
                # Broadcast update
                await self.websocket_manager.broadcast({
                    "type": "start_date_updated",
                    "data": {
                        "start_date": new_start_date,
                        "start_pokemon_id": start_pokemon_id,
                        "current_pokemon": self.pokemon_calendar.get_current_pokemon(),
                        "timestamp": datetime.now().isoformat()
                    }
                })
                
                return {
                    "success": True, 
                    "start_date": new_start_date,
                    "start_pokemon_id": start_pokemon_id,
                    "current_pokemon": self.pokemon_calendar.get_current_pokemon()
                }
                
            except Exception as e:
                logging.error(f"Failed to set start date: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to set start date: {str(e)}")

        @self.app.get("/api/schedule", response_model=List[PokemonScheduleEntry])
        async def get_schedule(days: int = 7):
            if not self.pokemon_calendar:
                raise HTTPException(status_code=503, detail="Pokemon calendar not available")
            
            if days < 1 or days > 30:
                raise HTTPException(status_code=400, detail="Days must be between 1 and 30")
            
            schedule = []
            today = datetime.now().date()
            
            for i in range(days):
                future_date = today + timedelta(days=i)
                pokemon = self.pokemon_calendar.get_pokemon_info_for_date(future_date)
                
                schedule.append(PokemonScheduleEntry(
                    date=future_date.strftime("%Y-%m-%d"),
                    day_name=future_date.strftime("%A"),
                    pokemon=PokemonInfo(
                        id=pokemon['id'],
                        name=pokemon['name'],
                        types=pokemon.get('types', []),
                        generation=pokemon.get('generation', 1),
                        local_sprite=pokemon.get('local_sprite')
                    ),
                    is_today=(i == 0)
                ))
            
            return schedule
        
        @self.app.get("/api/display-types")
        async def get_display_types():
            """Get available display types and current configuration"""
            if not self.pokemon_calendar:
                raise HTTPException(status_code=503, detail="Pokemon calendar not available")
            
            # Available display types with their specifications
            display_types = {
                "7in5_HD": {
                    "name": "7.5\" HD Monochrome",
                    "resolution": "880x528",
                    "colors": 2,
                    "color_mode": "monochrome",
                    "description": "High-resolution black and white e-ink display"
                },
                "7in3e": {
                    "name": "7.3\" ACeP 7-Color",
                    "resolution": "800x480", 
                    "colors": 7,
                    "color_mode": "7color",
                    "color_palette": ["BLACK", "WHITE", "GREEN", "BLUE", "RED", "YELLOW", "ORANGE"],
                    "description": "Advanced Color ePaper with vibrant 7-color display"
                }
            }
            
            current_config = {
                "display_type": getattr(self.pokemon_calendar, 'display_type', '7in5_HD'),
                "color_mode": getattr(self.pokemon_calendar, 'color_mode', 'monochrome'),
                "width": self.pokemon_calendar.display_width,
                "height": self.pokemon_calendar.display_height,
                "hardware_available": self.pokemon_calendar.epd is not None,
                "epd_type": getattr(self.pokemon_calendar, 'epd_type', None)
            }
            
            return {
                "available_types": display_types,
                "current": current_config,
                "color_mapper_initialized": hasattr(self.pokemon_calendar, 'color_mapper') and self.pokemon_calendar.color_mapper is not None
            }
        
        @self.app.post("/api/display-type/{display_type}")
        async def set_display_type(display_type: str):
            """Switch display type and reinitialize hardware"""
            if not self.pokemon_calendar:
                raise HTTPException(status_code=503, detail="Pokemon calendar not available")
            
            valid_types = ["7in5_HD", "7in3e"]
            if display_type not in valid_types:
                raise HTTPException(status_code=400, detail=f"Invalid display type. Must be one of: {valid_types}")
            
            # Update configuration
            config_update = ConfigUpdate(display={"type": display_type})
            
            # This will trigger the display type change logic in the config update handler
            old_display_type = self.pokemon_calendar.config.get('display', {}).get('type', '7in5_HD')
            self.pokemon_calendar.config.setdefault('display', {})['type'] = display_type
            
            # Auto-configure dimensions and color mode
            if display_type == '7in3e':
                self.pokemon_calendar.config['display'].update({
                    'width': 800,
                    'height': 480,
                    'color_mode': '7color'
                })
            elif display_type == '7in5_HD':
                self.pokemon_calendar.config['display'].update({
                    'width': 880,
                    'height': 528,
                    'color_mode': 'monochrome'
                })
            
            # Save configuration
            try:
                with open(self.pokemon_calendar.config_file, 'w') as f:
                    json.dump(self.pokemon_calendar.config, f, indent=2, default=str)
            except Exception as e:
                logging.warning(f"Failed to save display type to config: {e}")
            
            # Update calendar properties
            self.pokemon_calendar.display_type = display_type
            self.pokemon_calendar.display_width = self.pokemon_calendar.config['display']['width']
            self.pokemon_calendar.display_height = self.pokemon_calendar.config['display']['height']
            self.pokemon_calendar.color_mode = self.pokemon_calendar.config['display']['color_mode']
            
            # Initialize/cleanup color mapper
            if display_type == '7in3e':
                if not self.pokemon_calendar.color_mapper:
                    from color_mapping import SevenColorMapper
                    self.pokemon_calendar.color_mapper = SevenColorMapper()
                    logging.info("Initialized color mapper for 7-color display")
            else:
                self.pokemon_calendar.color_mapper = None
            
            # Attempt hardware reinitialization
            hardware_status = "simulation"
            try:
                if self.pokemon_calendar.epd:
                    if hasattr(self.pokemon_calendar.epd, 'sleep'):
                        self.pokemon_calendar.epd.sleep()
                
                from waveshare_epd import epd7in5_HD, epd7in3e
                self.pokemon_calendar.epd = None
                self.pokemon_calendar.epd_type = None
                
                if display_type == '7in5_HD' and epd7in5_HD:
                    self.pokemon_calendar.epd = epd7in5_HD.EPD()
                    self.pokemon_calendar.epd.init()
                    self.pokemon_calendar.epd.Clear()
                    self.pokemon_calendar.epd_type = '7in5_HD'
                    hardware_status = "initialized"
                    logging.info("Reinitialized 7.5\" HD display")
                elif display_type == '7in3e' and epd7in3e:
                    self.pokemon_calendar.epd = epd7in3e.EPD()
                    self.pokemon_calendar.epd.init()
                    self.pokemon_calendar.epd.Clear()
                    self.pokemon_calendar.epd_type = '7in3e'
                    hardware_status = "initialized"
                    logging.info("Reinitialized 7.3\" 7-color display")
                
            except Exception as e:
                logging.warning(f"Could not initialize {display_type} hardware: {e}")
                hardware_status = f"error: {str(e)}"
            
            # Broadcast change to all connected clients
            await self.websocket_manager.broadcast({
                "type": "display_type_changed",
                "data": {
                    "old_type": old_display_type,
                    "new_type": display_type,
                    "hardware_status": hardware_status,
                    "timestamp": datetime.now().isoformat()
                }
            })
            
            return {
                "success": True,
                "display_type": display_type,
                "hardware_status": hardware_status,
                "config_updated": True
            }

        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            await self.websocket_manager.connect(websocket)
            
            # Start message checker when first client connects
            if not self._message_check_running:
                asyncio.create_task(self._start_message_checker())
            
            try:
                # Send initial status
                if self.pokemon_calendar:
                    current_pokemon = self.pokemon_calendar.get_current_pokemon()
                    await websocket.send_text(json.dumps({
                        "type": "status",
                        "data": {
                            "current_pokemon": current_pokemon,
                            "demo_mode": self.pokemon_calendar.demo_mode,
                            "timestamp": datetime.now().isoformat()
                        }
                    }))
                
                # Keep connection alive and handle incoming messages
                while True:
                    try:
                        data = await websocket.receive_text()
                        logging.info(f"Received WebSocket message: {data}")
                        # Echo back or handle specific client messages if needed
                    except Exception as e:
                        logging.warning(f"WebSocket receive error: {e}")
                        break
                        
            except WebSocketDisconnect:
                self.websocket_manager.disconnect(websocket)

    async def _serve_web_interface(self):
        # Return a basic HTML interface (will enhance this later)
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Pokemon E-ink Calendar Control</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; }
                .container { max-width: 1200px; margin: 0 auto; }
                .card { background: white; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
                h1, h2 { color: #333; }
                .status-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; }
                .status-item { padding: 15px; background: #f8f9fa; border-radius: 6px; }
                .status-label { font-weight: bold; color: #666; font-size: 14px; }
                .status-value { font-size: 18px; color: #333; margin-top: 5px; }
                button { background: #007bff; color: white; border: none; padding: 12px 20px; border-radius: 6px; cursor: pointer; margin: 5px; }
                button:hover { background: #0056b3; }
                button.secondary { background: #6c757d; }
                button.secondary:hover { background: #545b62; }
                .pokemon-info { display: flex; align-items: center; gap: 20px; }
                .pokemon-sprite { 
                    width: 300px; 
                    height: 180px; 
                    background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%); 
                    border: 2px solid #dee2e6;
                    border-radius: 12px; 
                    display: flex; 
                    align-items: center; 
                    justify-content: center; 
                    font-size: 48px;
                    flex-shrink: 0;
                    box-shadow: 0 2px 8px rgba(0,0,0,0.1);
                    overflow: hidden;
                    position: relative;
                }
                .pokemon-sprite img {
                    width: 100%;
                    height: 100%;
                    object-fit: contain;
                    border-radius: 10px;
                }
                .pokemon-sprite .loading-text {
                    position: absolute;
                    color: #666;
                    font-size: 14px;
                    font-weight: 500;
                }
                .config-section { background: #f8f9fa; padding: 20px; border-radius: 8px; margin: 15px 0; }
                .config-row { display: flex; align-items: center; gap: 15px; margin-bottom: 15px; flex-wrap: wrap; }
                .config-label { font-weight: bold; color: #333; min-width: 120px; }
                .config-input { padding: 10px; border: 2px solid #dee2e6; border-radius: 6px; font-size: 14px; }
                .config-input:focus { border-color: #007bff; outline: none; }
                .config-help { font-size: 12px; color: #6c757d; margin-top: 5px; }
                .date-picker { width: 150px; }
                .pokemon-picker { width: 80px; }
                .config-checkbox { 
                    display: flex; 
                    align-items: center; 
                    gap: 6px; 
                    margin-left: 10px;
                    font-weight: normal;
                    color: #333;
                    cursor: pointer;
                }
                .config-checkbox input[type="checkbox"] {
                    margin: 0;
                    cursor: pointer;
                }
                button.config-btn { background: #17a2b8; margin-left: 10px; }
                button.config-btn:hover { background: #138496; }
                .pokemon-details { flex: 1; }
                .pokemon-details h3 { 
                    margin: 0 0 8px 0; 
                    color: #333; 
                    font-size: 28px;
                    font-weight: bold;
                }
                .pokemon-id { 
                    color: #6c757d; 
                    font-size: 16px; 
                    font-weight: 500;
                    margin-bottom: 12px;
                }
                .pokemon-types { display: flex; gap: 8px; flex-wrap: wrap; }
                .type-badge { 
                    padding: 6px 16px; 
                    border-radius: 20px; 
                    font-size: 12px; 
                    font-weight: bold; 
                    color: white;
                    text-transform: uppercase;
                    letter-spacing: 0.5px;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.2);
                }
                .loading { opacity: 0.6; pointer-events: none; }
                #status { color: #28a745; font-weight: bold; }
                .api-info { background: #e9ecef; padding: 15px; border-radius: 6px; margin-top: 20px; }
                .api-info h3 { margin-top: 0; }
                .api-info code { background: #f8f9fa; padding: 2px 6px; border-radius: 3px; }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="card">
                    <h1>🎮 Pokemon E-ink Calendar Control</h1>
                    <div id="status">Connecting...</div>
                </div>
                
                <div class="card" id="current-pokemon">
                    <h2>Current Pokemon <button onclick="refreshDisplayPreview()" class="refresh-preview-btn">🔄 Refresh Preview</button></h2>
                    <div class="pokemon-info">
                        <div class="pokemon-sprite" id="pokemon-sprite">
                            <div class="loading-text">Loading display preview...</div>
                        </div>
                        <div class="pokemon-details">
                            <h3 id="pokemon-name">Loading...</h3>
                            <div class="pokemon-id" id="pokemon-id">#000</div>
                            <div id="pokemon-types"></div>
                        </div>
                    </div>
                </div>
                
                <div class="card">
                    <h2>System Status</h2>
                    <div class="status-grid" id="status-grid">
                        <div class="status-item">
                            <div class="status-label">Demo Mode</div>
                            <div class="status-value" id="demo-mode">Unknown</div>
                        </div>
                        <div class="status-item">
                            <div class="status-label">Display Type</div>
                            <div class="status-value" id="display-type">Unknown</div>
                        </div>
                        <div class="status-item">
                            <div class="status-label">Display Size</div>
                            <div class="status-value" id="display-size">Unknown</div>
                        </div>
                        <div class="status-item">
                            <div class="status-label">Color Mode</div>
                            <div class="status-value" id="color-mode">Unknown</div>
                        </div>
                        <div class="status-item">
                            <div class="status-label">Total Pokemon</div>
                            <div class="status-value" id="pokemon-count">Unknown</div>
                        </div>
                        <div class="status-item">
                            <div class="status-label">E-ink Display</div>
                            <div class="status-value" id="epd-status">Unknown</div>
                        </div>
                    </div>
                </div>
                
                <div class="card">
                    <h2>Configuration</h2>
                    <div class="config-section">
                        <h3>Display Settings</h3>
                        
                        <!-- Display Type Selection -->
                        <div class="config-row">
                            <span class="config-label">Display Type:</span>
                            <select id="display-type-select" class="config-input" onchange="displayTypeChanged()">
                                <option value="7in5_HD">7.5" HD Monochrome (880x528)</option>
                                <option value="7in3e">7.3" ACeP 7-Color (800x480)</option>
                                <option value="7in5_V2">7.5" HD Monochrome (800x480)</option>
                            </select>
                            <button id="switch-display-type-btn" onclick="switchDisplayType()" class="config-btn">🖥️ Switch Display</button>
                        </div>
                        <div class="config-help" id="display-type-help">
                            Choose your e-ink display type. This will automatically adjust resolution, color processing, and hardware settings.
                        </div>
                        
                        <!-- Color Mode Status -->
                        <div class="config-row" id="color-mode-row" style="margin-top: 10px;">
                            <span class="config-label">Color Mode:</span>
                            <span id="color-mode-status" class="status-value" style="font-size: 14px; margin: 0;">Unknown</span>
                            <div id="color-palette" class="color-palette" style="display: none; margin-left: 15px;">
                                <span style="background: #000; width: 20px; height: 20px; display: inline-block; border-radius: 3px; margin: 0 2px;" title="BLACK"></span>
                                <span style="background: #fff; width: 20px; height: 20px; display: inline-block; border-radius: 3px; margin: 0 2px; border: 1px solid #ccc;" title="WHITE"></span>
                                <span style="background: #0f0; width: 20px; height: 20px; display: inline-block; border-radius: 3px; margin: 0 2px;" title="GREEN"></span>
                                <span style="background: #00f; width: 20px; height: 20px; display: inline-block; border-radius: 3px; margin: 0 2px;" title="BLUE"></span>
                                <span style="background: #f00; width: 20px; height: 20px; display: inline-block; border-radius: 3px; margin: 0 2px;" title="RED"></span>
                                <span style="background: #ff0; width: 20px; height: 20px; display: inline-block; border-radius: 3px; margin: 0 2px;" title="YELLOW"></span>
                                <span style="background: #fa0; width: 20px; height: 20px; display: inline-block; border-radius: 3px; margin: 0 2px;" title="ORANGE"></span>
                            </div>
                        </div>
                        
                        <div class="config-row">
                            <span class="config-label">Border Inset:</span>
                            <input type="number" id="border-inset-input" class="config-input" min="0" max="100" value="0" />
                            <span class="config-label">pixels</span>
                            <label class="config-checkbox">
                                <input type="checkbox" id="border-inset-enabled" checked />
                                Enabled
                            </label>
                            <button id="update-border-inset-btn" onclick="updateBorderInset()" class="config-btn">🖼️ Update Border</button>
                        </div>
                        <div class="config-help">
                            Add a white border around the display content. Useful for framing the image or testing different display sizes (0-100 pixels).
                        </div>
                    </div>
                    <div class="config-section">
                        <h3>Calendar Start Date</h3>
                        <div class="config-row">
                            <span class="config-label">Start Date:</span>
                            <input type="date" id="start-date-input" class="config-input date-picker" />
                            <span class="config-label">Starting Pokemon #:</span>
                            <input type="number" id="start-pokemon-input" class="config-input pokemon-picker" min="1" max="1025" value="1" />
                            <button id="update-start-date-btn" onclick="updateStartDate()" class="config-btn">📅 Update Start Date</button>
                        </div>
                        <div class="config-help">
                            Set which Pokemon appears on which date. For example, setting start date to today with Pokemon #1 (Bulbasaur) means Bulbasaur will appear today, Ivysaur tomorrow, etc.
                        </div>
                    </div>
                </div>
                
                <div class="card">
                    <h2>Controls</h2>
                    <button id="update-display-btn" onclick="updateDisplay()">🔄 Update Display</button>
                    <button onclick="toggleDemo()" id="demo-toggle">🎬 Toggle Demo Mode</button>
                    <button id="refresh-status-btn" onclick="refreshStatus()" class="secondary">📊 Refresh Status</button>
                </div>
                
                <div class="card api-info">
                    <h3>API Endpoints</h3>
                    <p>This web interface provides access to the Pokemon Calendar API:</p>
                    <ul>
                        <li><code>GET /api/status</code> - System status and current Pokemon</li>
                        <li><code>GET /api/config</code> - Current configuration</li>
                        <li><code>POST /api/config</code> - Update configuration</li>
                        <li><code>POST /api/update-display</code> - Force display update</li>
                        <li><code>POST /api/set-start-date</code> - Update calendar start date</li>
                        <li><code>GET /api/pokemon</code> - List all Pokemon (with search/pagination)</li>
                        <li><code>GET /api/schedule?days=7</code> - Get upcoming Pokemon schedule</li>
                        <li><code>WebSocket /ws</code> - Real-time updates</li>
                    </ul>
                </div>
            </div>
            
            <script>
                let ws = null;
                let currentStatus = null;
                
                // WebSocket connection
                function connectWebSocket() {
                    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                    const wsUrl = `${protocol}//${window.location.host}/ws`;
                    
                    ws = new WebSocket(wsUrl);
                    
                    ws.onopen = function() {
                        document.getElementById('status').textContent = 'Connected ✅';
                        document.getElementById('status').style.color = '#28a745';
                        refreshStatus();
                        console.log('WebSocket connected');
                    };
                    
                    ws.onmessage = function(event) {
                        console.log('Raw WebSocket message received:', event.data);
                        try {
                            const message = JSON.parse(event.data);
                            console.log('Parsed WebSocket message:', message);
                            handleWebSocketMessage(message);
                        } catch (e) {
                            console.error('Failed to parse WebSocket message:', e, event.data);
                        }
                    };
                    
                    ws.onclose = function() {
                        document.getElementById('status').textContent = 'Disconnected ❌';
                        document.getElementById('status').style.color = '#dc3545';
                        // Reconnect after 3 seconds
                        setTimeout(connectWebSocket, 3000);
                    };
                    
                    ws.onerror = function() {
                        document.getElementById('status').textContent = 'Connection Error ⚠️';
                        document.getElementById('status').style.color = '#ffc107';
                    };
                }
                
                function handleWebSocketMessage(message) {
                    console.log('WebSocket message:', message);
                    
                    if (message.type === 'status') {
                        if (message.data) {
                            updateStatusDisplay(message.data);
                            loadDisplayPreview(); // Refresh preview on status updates
                        }
                    } else if (message.type === 'display_updated') {
                        // For display updates, only update Pokemon info and refresh preview
                        if (message.data && message.data.current_pokemon) {
                            updatePokemonInfo(message.data.current_pokemon);
                            if (message.data.demo_mode !== undefined) {
                                updateDemoModeDisplay(message.data.demo_mode);
                            }
                        }
                        // Always refresh display preview when display is updated
                        console.log('Display updated - refreshing preview');
                        loadDisplayPreview();
                    } else if (message.type === 'demo_mode_changed') {
                        updateDemoModeDisplay(message.enabled);
                        if (message.data && message.data.current_pokemon) {
                            updatePokemonInfo(message.data.current_pokemon);
                        }
                        // Refresh preview when demo mode changes
                        loadDisplayPreview();
                    } else if (message.type === 'display_preview_updated') {
                        console.log('Preview updated - refreshing display');
                        loadDisplayPreview();
                    } else if (message.type === 'start_date_updated') {
                        console.log('Start date updated');
                        if (message.data && message.data.current_pokemon) {
                            updatePokemonInfo(message.data.current_pokemon);
                        }
                        loadDisplayPreview();
                        // Update the input fields to reflect new values
                        if (message.data.start_date) {
                            document.getElementById('start-date-input').value = message.data.start_date;
                        }
                        if (message.data.start_pokemon_id) {
                            document.getElementById('start-pokemon-input').value = message.data.start_pokemon_id;
                        }
                    } else if (message.type === 'display_type_changed') {
                        console.log('Display type changed:', message.data);
                        // Refresh status to get updated display configuration
                        refreshStatus();
                        // Refresh display preview as color processing may have changed
                        loadDisplayPreview();
                        // Show notification about the change
                        if (message.data) {
                            const helpText = document.getElementById('display-type-help');
                            if (helpText) {
                                let statusMessage = `Display switched from ${message.data.old_type} to ${message.data.new_type}`;
                                if (message.data.hardware_status === 'initialized') {
                                    statusMessage += ' (Hardware reinitialized)';
                                } else if (message.data.hardware_status.startsWith('error')) {
                                    statusMessage += ' (Simulation mode)';
                                }
                                
                                helpText.innerHTML = `✅ ${statusMessage}`;
                                helpText.style.color = '#155724';
                                helpText.style.backgroundColor = '#d4edda';
                                helpText.style.padding = '10px';
                                helpText.style.borderRadius = '6px';
                                
                                setTimeout(() => {
                                    helpText.innerHTML = 'Choose your e-ink display type. This will automatically adjust resolution, color processing, and hardware settings.';
                                    helpText.style.color = '';
                                    helpText.style.backgroundColor = '';
                                    helpText.style.padding = '';
                                    helpText.style.borderRadius = '';
                                }, 3000);
                            }
                        }
                    }
                }
                
                // Update just Pokemon info (separate from full status)
                function updatePokemonInfo(pokemon) {
                    document.getElementById('pokemon-name').textContent = pokemon.name;
                    document.getElementById('pokemon-id').textContent = `#${pokemon.id.toString().padStart(3, '0')}`;
                    
                    // Update types
                    const typesContainer = document.getElementById('pokemon-types');
                    typesContainer.innerHTML = '';
                    if (pokemon.types && pokemon.types.length > 0) {
                        pokemon.types.forEach(type => {
                            const badge = document.createElement('span');
                            badge.className = 'type-badge';
                            badge.textContent = type.toUpperCase();
                            badge.style.backgroundColor = getTypeColor(type);
                            typesContainer.appendChild(badge);
                        });
                    }
                }
                
                // Load display preview image
                function loadDisplayPreview() {
                    console.log('Loading display preview...');
                    const spriteElement = document.getElementById('pokemon-sprite');
                    
                    // Clear existing content completely
                    spriteElement.innerHTML = '<div class="loading-text">Loading preview...</div>';
                    
                    // Use fetch to get the image as blob to bypass cache completely
                    const timestamp = Date.now();
                    const random = Math.random().toString(36).substring(7);
                    const imageUrl = `/api/current-display?t=${timestamp}&r=${random}`;
                    
                    console.log(`Fetching fresh image: ${imageUrl}`);
                    
                    fetch(imageUrl, {
                        method: 'GET',
                        cache: 'no-cache',
                        headers: {
                            'Cache-Control': 'no-cache',
                            'Pragma': 'no-cache'
                        }
                    })
                    .then(response => {
                        if (!response.ok) {
                            throw new Error(`HTTP ${response.status}`);
                        }
                        return response.blob();
                    })
                    .then(blob => {
                        const img = document.createElement('img');
                        const objectUrl = URL.createObjectURL(blob);
                        
                        img.onload = function() {
                            console.log('Display preview loaded successfully via fetch');
                            spriteElement.innerHTML = '';
                            spriteElement.appendChild(img);
                            // Clean up the object URL
                            URL.revokeObjectURL(objectUrl);
                        };
                        
                        img.onerror = function() {
                            console.error('Failed to display fetched image');
                            spriteElement.innerHTML = '<div class="loading-text">Preview not available</div>';
                            URL.revokeObjectURL(objectUrl);
                        };
                        
                        img.src = objectUrl;
                    })
                    .catch(error => {
                        console.error('Failed to fetch display preview:', error);
                        spriteElement.innerHTML = '<div class="loading-text">Preview failed to load</div>';
                        
                        // Fallback: try the old method
                        setTimeout(() => {
                            console.log('Falling back to img.src method...');
                            const img = document.createElement('img');
                            img.onload = function() {
                                spriteElement.innerHTML = '';
                                spriteElement.appendChild(img);
                            };
                            img.src = imageUrl;
                        }, 1000);
                    });
                }
                
                // API functions
                async function refreshStatus() {
                    try {
                        const response = await fetch('/api/status');
                        if (response.ok) {
                            const status = await response.json();
                            currentStatus = status;
                            updateStatusDisplay(status);
                            loadDisplayPreview(); // Also refresh display preview
                        }
                    } catch (error) {
                        console.error('Failed to fetch status:', error);
                    }
                }
                
                async function refreshDisplayPreview() {
                    const button = event.target;
                    const originalText = button.textContent;
                    button.disabled = true;
                    button.textContent = '🔄 Refreshing...';
                    
                    try {
                        const response = await fetch('/api/refresh-display-preview', { method: 'POST' });
                        if (response.ok) {
                            // The WebSocket will handle the actual image refresh
                            button.textContent = '✅ Refreshed!';
                            setTimeout(() => {
                                button.textContent = originalText;
                                button.disabled = false;
                            }, 2000);
                        } else {
                            throw new Error('Refresh failed');
                        }
                    } catch (error) {
                        button.textContent = '❌ Failed';
                        button.disabled = false;
                        console.error('Failed to refresh display preview:', error);
                        setTimeout(() => {
                            button.textContent = originalText;
                        }, 2000);
                    }
                }
                
                async function updateDisplay() {
                    const button = event.target;
                    button.disabled = true;
                    button.textContent = '🔄 Updating...';
                    
                    try {
                        const response = await fetch('/api/update-display', { method: 'POST' });
                        if (response.ok) {
                            button.textContent = '✅ Updated!';
                            setTimeout(() => {
                                button.textContent = '🔄 Update Display';
                                button.disabled = false;
                            }, 2000);
                        } else {
                            throw new Error('Update failed');
                        }
                    } catch (error) {
                        button.textContent = '❌ Failed';
                        button.disabled = false;
                        console.error('Failed to update display:', error);
                        setTimeout(() => {
                            button.textContent = '🔄 Update Display';
                        }, 2000);
                    }
                }
                
                async function updateStartDate() {
                    const startDateInput = document.getElementById('start-date-input');
                    const startPokemonInput = document.getElementById('start-pokemon-input');
                    const button = event.target;
                    
                    const startDate = startDateInput.value;
                    const startPokemonId = parseInt(startPokemonInput.value);
                    
                    if (!startDate) {
                        alert('Please select a start date');
                        return;
                    }
                    
                    if (!startPokemonId || startPokemonId < 1 || startPokemonId > 1025) {
                        alert('Please enter a valid Pokemon ID (1-1025)');
                        return;
                    }
                    
                    button.disabled = true;
                    button.textContent = '📅 Updating...';
                    
                    try {
                        const response = await fetch('/api/set-start-date', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json'
                            },
                            body: JSON.stringify({
                                start_date: startDate,
                                start_pokemon_id: startPokemonId
                            })
                        });
                        
                        if (response.ok) {
                            const result = await response.json();
                            button.textContent = '✅ Updated!';
                            console.log('Start date updated successfully:', result);
                            
                            setTimeout(() => {
                                button.textContent = '📅 Update Start Date';
                                button.disabled = false;
                            }, 2000);
                        } else {
                            const error = await response.json();
                            throw new Error(error.detail || 'Update failed');
                        }
                    } catch (error) {
                        button.textContent = '❌ Failed';
                        console.error('Failed to update start date:', error);
                        alert(`Failed to update start date: ${error.message}`);
                        
                        setTimeout(() => {
                            button.textContent = '📅 Update Start Date';
                            button.disabled = false;
                        }, 2000);
                    }
                }
                
                async function updateBorderInset() {
                    const borderInsetInput = document.getElementById('border-inset-input');
                    const borderInsetEnabled = document.getElementById('border-inset-enabled');
                    const button = event.target;
                    
                    const insetPixels = parseInt(borderInsetInput.value);
                    const enabled = borderInsetEnabled.checked;
                    
                    if (isNaN(insetPixels) || insetPixels < 0 || insetPixels > 100) {
                        alert('Please enter a valid border inset value (0-100 pixels)');
                        return;
                    }
                    
                    button.disabled = true;
                    button.textContent = '🖼️ Updating...';
                    
                    try {
                        const response = await fetch('/api/config', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json'
                            },
                            body: JSON.stringify({
                                display: {
                                    border_inset: {
                                        enabled: enabled,
                                        pixels: insetPixels
                                    }
                                }
                            })
                        });
                        
                        if (response.ok) {
                            const result = await response.json();
                            button.textContent = '✅ Updated!';
                            console.log('Border inset updated successfully:', result);
                            
                            // Immediately refresh the display preview
                            setTimeout(() => {
                                refreshDisplayPreview();
                            }, 500);
                            
                            setTimeout(() => {
                                button.textContent = '🖼️ Update Border';
                                button.disabled = false;
                            }, 2000);
                        } else {
                            const error = await response.json();
                            throw new Error(error.detail || 'Update failed');
                        }
                    } catch (error) {
                        button.textContent = '❌ Failed';
                        console.error('Failed to update border inset:', error);
                        alert(`Failed to update border inset: ${error.message}`);
                        
                        setTimeout(() => {
                            button.textContent = '🖼️ Update Border';
                            button.disabled = false;
                        }, 2000);
                    }
                }
                
                async function toggleDemo() {
                    if (!currentStatus) return;
                    
                    const newMode = !currentStatus.demo_mode;
                    const button = document.getElementById('demo-toggle');
                    button.disabled = true;
                    
                    try {
                        const response = await fetch(`/api/demo-mode/${newMode}`, { method: 'POST' });
                        if (response.ok) {
                            const result = await response.json();
                            updateDemoModeDisplay(result.demo_mode);
                        }
                    } catch (error) {
                        console.error('Failed to toggle demo mode:', error);
                    } finally {
                        button.disabled = false;
                    }
                }
                
                // UI update functions
                function updateStatusDisplay(status) {
                    // Update current Pokemon info if present
                    if (status.current_pokemon) {
                        updatePokemonInfo(status.current_pokemon);
                    }
                    
                    // Update system status fields only if they exist
                    if (status.demo_mode !== undefined) {
                        document.getElementById('demo-mode').textContent = status.demo_mode ? 'Enabled' : 'Disabled';
                        // Also update the demo toggle button text
                        updateDemoModeDisplay(status.demo_mode);
                    }
                    if (status.display_type !== undefined) {
                        const displayTypeMap = {
                            '7in5_HD': '7.5" HD Monochrome',
                            '7in3e': '7.3" ACeP 7-Color'
                        };
                        document.getElementById('display-type').textContent = displayTypeMap[status.display_type] || status.display_type;
                        // Update display type selector
                        const select = document.getElementById('display-type-select');
                        if (select && select.value !== status.display_type) {
                            select.value = status.display_type;
                        }
                    }
                    if (status.display_width && status.display_height) {
                        document.getElementById('display-size').textContent = `${status.display_width} × ${status.display_height}`;
                    }
                    if (status.color_mode !== undefined) {
                        const colorModeMap = {
                            'monochrome': 'Monochrome (B&W)',
                            '7color': '7-Color ACeP'
                        };
                        document.getElementById('color-mode').textContent = colorModeMap[status.color_mode] || status.color_mode;
                        // Update color mode status in config section
                        const colorModeStatus = document.getElementById('color-mode-status');
                        const colorPalette = document.getElementById('color-palette');
                        if (colorModeStatus) {
                            colorModeStatus.textContent = colorModeMap[status.color_mode] || status.color_mode;
                        }
                        if (colorPalette) {
                            colorPalette.style.display = status.color_mode === '7color' ? 'inline-block' : 'none';
                        }
                    }
                    if (status.total_pokemon_count !== undefined) {
                        document.getElementById('pokemon-count').textContent = status.total_pokemon_count.toLocaleString();
                    }
                    if (status.epd_available !== undefined) {
                        document.getElementById('epd-status').textContent = status.epd_available ? 'Connected' : 'Simulated';
                    }
                    
                    // Update current status reference
                    if (currentStatus) {
                        // Merge new data with existing status
                        currentStatus = { ...currentStatus, ...status };
                    } else {
                        currentStatus = status;
                    }
                }
                
                function updateDemoModeDisplay(enabled) {
                    document.getElementById('demo-mode').textContent = enabled ? 'Enabled' : 'Disabled';
                    const button = document.getElementById('demo-toggle');
                    button.textContent = enabled ? '⏹️ Stop Demo Mode' : '▶️ Start Demo Mode';
                    if (currentStatus) {
                        currentStatus.demo_mode = enabled;
                    }
                }
                
                function getTypeColor(type) {
                    const colors = {
                        normal: '#A8A878', fighting: '#C03028', flying: '#A890F0', poison: '#A040A0',
                        ground: '#E0C068', rock: '#B8A038', bug: '#A8B820', ghost: '#705898',
                        steel: '#B8B8D0', fire: '#F08030', water: '#6890F0', grass: '#78C850',
                        electric: '#F8D030', psychic: '#F85888', ice: '#98D8D8', dragon: '#7038F8',
                        dark: '#705848', fairy: '#EE99AC'
                    };
                    return colors[type.toLowerCase()] || '#68A090';
                }
                
                // Initialize configuration form
                async function initializeConfigForm() {
                    try {
                        const response = await fetch('/api/config');
                        if (response.ok) {
                            const config = await response.json();
                            
                            // Set border inset settings
                            if (config.display && config.display.border_inset) {
                                const borderInset = config.display.border_inset;
                                document.getElementById('border-inset-input').value = borderInset.pixels || 0;
                                document.getElementById('border-inset-enabled').checked = borderInset.enabled !== false;
                            } else {
                                // Set defaults
                                document.getElementById('border-inset-input').value = 0;
                                document.getElementById('border-inset-enabled').checked = true;
                            }
                            
                            // Set start date
                            if (config.pokemon && config.pokemon.start_date) {
                                document.getElementById('start-date-input').value = config.pokemon.start_date;
                            }
                            
                            // Set start Pokemon ID
                            if (config.pokemon && config.pokemon.start_pokemon_id) {
                                document.getElementById('start-pokemon-input').value = config.pokemon.start_pokemon_id;
                            } else {
                                document.getElementById('start-pokemon-input').value = 1;
                            }
                        }
                    } catch (error) {
                        console.error('Failed to load configuration for form:', error);
                    }
                }
                
                // Display Type Management Functions
                function displayTypeChanged() {
                    const select = document.getElementById('display-type-select');
                    const helpText = document.getElementById('display-type-help');
                    const switchButton = document.getElementById('switch-display-type-btn');
                    
                    if (currentStatus && select.value !== currentStatus.display_type) {
                        helpText.innerHTML = `⚠️ <strong>Display type will change to ${select.options[select.selectedIndex].text}</strong><br>Click "Switch Display" to apply changes. This will update resolution, color processing, and hardware settings.`;
                        helpText.style.color = '#856404';
                        helpText.style.backgroundColor = '#fff3cd';
                        helpText.style.padding = '10px';
                        helpText.style.borderRadius = '6px';
                        switchButton.style.backgroundColor = '#fd7e14';
                        switchButton.textContent = '🔄 Apply Changes';
                    } else {
                        helpText.innerHTML = 'Choose your e-ink display type. This will automatically adjust resolution, color processing, and hardware settings.';
                        helpText.style.color = '';
                        helpText.style.backgroundColor = '';
                        helpText.style.padding = '';
                        helpText.style.borderRadius = '';
                        switchButton.style.backgroundColor = '';
                        switchButton.textContent = '🖥️ Switch Display';
                    }
                }
                
                async function switchDisplayType() {
                    const select = document.getElementById('display-type-select');
                    const button = document.getElementById('switch-display-type-btn');
                    const originalText = button.textContent;
                    
                    button.disabled = true;
                    button.textContent = '🔄 Switching...';
                    
                    try {
                        const response = await fetch(`/api/display-type/${select.value}`, { 
                            method: 'POST' 
                        });
                        
                        if (response.ok) {
                            const result = await response.json();
                            button.textContent = '✅ Switched!';
                            
                            // Show success message with hardware status
                            const helpText = document.getElementById('display-type-help');
                            let statusMessage = `Successfully switched to ${select.options[select.selectedIndex].text}`;
                            if (result.hardware_status === 'initialized') {
                                statusMessage += ' (Hardware initialized)';
                            } else if (result.hardware_status.startsWith('error')) {
                                statusMessage += ' (Simulation mode - hardware not available)';
                            }
                            
                            helpText.innerHTML = `✅ ${statusMessage}`;
                            helpText.style.color = '#155724';
                            helpText.style.backgroundColor = '#d4edda';
                            helpText.style.padding = '10px';
                            helpText.style.borderRadius = '6px';
                            
                            // Refresh status to get updated display info
                            setTimeout(refreshStatus, 500);
                            
                            // Reset success message after 3 seconds
                            setTimeout(() => {
                                helpText.innerHTML = 'Choose your e-ink display type. This will automatically adjust resolution, color processing, and hardware settings.';
                                helpText.style.color = '';
                                helpText.style.backgroundColor = '';
                                helpText.style.padding = '';
                                helpText.style.borderRadius = '';
                                button.textContent = '🖥️ Switch Display';
                                button.disabled = false;
                            }, 3000);
                        } else {
                            const error = await response.json();
                            throw new Error(error.detail || 'Switch failed');
                        }
                    } catch (error) {
                        button.textContent = '❌ Failed';
                        console.error('Failed to switch display type:', error);
                        
                        const helpText = document.getElementById('display-type-help');
                        helpText.innerHTML = `❌ Failed to switch display type: ${error.message}`;
                        helpText.style.color = '#721c24';
                        helpText.style.backgroundColor = '#f8d7da';
                        helpText.style.padding = '10px';
                        helpText.style.borderRadius = '6px';
                        
                        setTimeout(() => {
                            helpText.innerHTML = 'Choose your e-ink display type. This will automatically adjust resolution, color processing, and hardware settings.';
                            helpText.style.color = '';
                            helpText.style.backgroundColor = '';
                            helpText.style.padding = '';
                            helpText.style.borderRadius = '';
                            button.textContent = originalText;
                            button.disabled = false;
                        }, 3000);
                    }
                }
                
                // Initialize
                connectWebSocket();
                
                // Load initial display preview and config form after a short delay
                setTimeout(() => {
                    loadDisplayPreview();
                    initializeConfigForm();
                }, 1000);
            </script>
        </body>
        </html>
        """

    def start(self):
        """Start the web server in a separate thread"""
        if self.server_thread and self.server_thread.is_alive():
            logging.warning("Web server is already running")
            return
        
        # Set up mDNS service broadcasting
        self._setup_mdns_service()
        
        def run_server():
            logging.info(f"Starting web server on {self.host}:{self.port}")
            self.server = uvicorn.run(
                self.app,
                host=self.host,
                port=self.port,
                log_level="info",
                access_log=False
            )
        
        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()
        logging.info(f"Web server started at http://{self.host}:{self.port}")

    def stop(self):
        """Stop the web server"""
        # Clean up mDNS service first
        self._cleanup_mdns_service()
        
        if self.server:
            self.server.should_exit = True
        if self.server_thread:
            self.server_thread.join(timeout=5)
        logging.info("Web server stopped")

    async def broadcast_update(self, message_type: str, data: dict):
        """Broadcast an update to all connected WebSocket clients"""
        await self.websocket_manager.broadcast({
            "type": message_type,
            "data": data,
            "timestamp": datetime.now().isoformat()
        })


if __name__ == "__main__":
    # For testing without the main calendar
    server = PokemonWebServer()
    server.start()
    
    try:
        # Keep the main thread alive
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Shutting down web server...")
        server.stop()