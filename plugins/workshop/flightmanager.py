
import numpy as np
import json
from datetime import datetime

import bluesky as bs
from bluesky.core import Entity, timed_function
from bluesky import stack
from bluesky.tools.aero import ft
from bluesky.tools.geo import qdrdist

from plugins.workshop import flightplanmaker as fp
from plugins.workshop import flighttelemetry as fte

flightmanager = None
def init_plugin():
    # Configuration parameters
    global flightmanager
    flightmanager = FlightManager()

    config = {
        'plugin_name': 'FLIGHTMANAGER',
        'plugin_type': 'sim'
    }
    return config

class FlightManager(Entity):
    def __init__(self):
        super().__init__()
        
        with self.settrafarrays():
            self.virtual_ac = np.array([], dtype=bool)
            self.pprz_ids = np.array([], dtype=str)
        
    def create(self, n=1):
        super().create(n)
        self.virtual_ac[-n:] = False
        self.pprz_ids[-n:] = ''
            
    def convert_to_virtual(self, acidx):
        self.virtual_ac[acidx] = True
        self.pprz_ids[acidx] = ''
        stack.stack(f'{bs.traf.id[acidx]} LNAV ON')
        return
    
    # @timed_function(dt=1.0)
    # def check_connections(self):
    #     now = datetime.now()
    #     # TODO: Only fo this check for real aircraft
    #     for acidx in range(bs.traf.ntraf):
    #         time_diff = now - fte.telemetry.last_telemetry_update[acidx]
    #         # If more than 5 seconds then we convert to virtual aircraft
    #         if time_diff.total_seconds() > 5:
    #             self.convert_to_virtual(acidx)
    
    def updateflightplan(self):
        ...
        # TODO: implement update flight plan
        # TODO: remember to send an update id if flight plan is updated
    
    def checkactiveflightplan(self):
        ...
        # TODO: implement check active flight plan.
        # TODO: ensure that flight plan maker matches telemetry id

    @stack.command()
    def takeoffac(self, acid: str):
        '''Takeoff'''
        self.send_command(acid, "TakeOff")

    @stack.command()
    def executefp(self, acid: str):
        '''Execute flight plan'''
        self.send_command(acid, "ExecuteFlightPlan")

    @stack.command()
    def holdac(self, acid: str):
        '''Hold'''
        self.send_command(acid, "Hold")

    @stack.command()
    def continuefp(self, acid: str):
        '''Continue'''
        self.send_command(acid, "Continue")

    def send_command(self, acid, command):
        
        # get idx of aircraft and pprz id
        acidx = bs.traf.id2idx(acid)
        pprz_id =self.pprz_ids[acidx]

        # create command
        command_dict = {"Command": f"{command}"}

        # send command
        fp.flightplans.mqtt_client.publish(f'control/command/{pprz_id}', json.dumps(command_dict))
