from lib2to3.pgen2 import driver
import bluesky as bs
from bluesky import settings, stack
from bluesky.core.simtime import timed_function
from bluesky.tools import aero, areafilter, geo
from rtree import index
from matplotlib.path import Path
import json
import numpy as np
import pandas as pd
from shapely import wkt
from shapely.geometry import Point
from shapely.ops import nearest_points
import geopandas as gpd

settings.set_variable_defaults(geofence_dtlookahead=30)

def init_plugin():
    # Configuration parameters
    config = {
        'plugin_name': 'GEOFENCE',
        'plugin_type': 'sim',
        'reset': Geofence.reset
    }
    return config

@stack.command()
def geofence(name: 'txt', top: float = 999999, bottom: float = -999999, *coordinates: float):
    ''' Create a new geofence from the stack. 
        
        Arguments:
        - name: The name of the new geofence
        - top: The top of the geofence in feet.
        - bottom: The bottom of the geofence in feet.
        - coordinates: three or more lat/lon coordinates in degrees.
    '''
    Geofence(name, coordinates, top, bottom)
    return True, f'Created geofence {name}'


@stack.command()
def delgeofence(name: 'txt'):
    ''' Delete a geofence.'''
    Geofence.delete(name)

@stack.command()
def savegeofences(filename: 'txt'):
    ''' Save the current loaded geofences.'''
    if filename[-5:] != '.json':
        filename = filename + '.json'
    with open(f'data/geofences/{filename}', 'w') as f:
        json.dump(Geofence.geo_save_dict, f, indent=4)
    bs.scr.echo(f'Geofences saved to {filename}.')

@stack.command()
def loadgeofences(filename: 'txt'):
    '''Load a geofence file.'''
    if filename[-5:] != '.json':
        filename = filename + '.json'
    with open(f'data/geofences/{filename}', 'r') as f:
        try:
            loaded_geo_dict = json.loads(f.read())
        except:
            bs.scr.echo(f'File empty or does not exist.')
            return
    for geofence in loaded_geo_dict.values():
        Geofence(geofence['name'], geofence['coordinates'], geofence['top'], geofence['bottom'])
    bs.scr.echo(f'Geofences loaded from {filename}.')

@stack.command()
def loadgeojson(filename: 'txt', name_col: 'txt', top_col: 'txt', bottom_col: 'txt'=None):
    '''Load geofences from a GeoJSON file. Must be in EPSG:4326 format.'''
    if filename[-5:] != '.geojson':
        filename = filename + '.geojson'
        filename = filename.lower()
    try:
        loaded_gpd = gpd.read_file(f'data/geofences/{filename}', driver='GeoJSON')
    except:
        bs.scr.echo(f'File empty or does not exist.')
        return

    # now try to check if the column exists and put in lower case
    try:
        if name_col not in loaded_gpd.columns:
            name_col = name_col.lower()

        if top_col not in loaded_gpd.columns:
            top_col = top_col.lower()

        if bottom_col:
            if bottom_col not in loaded_gpd.columns:
                bottom_col = bottom_col.lower()
        else:
            bottom_col = 'bottom'
            loaded_gpd[bottom_col] = 0.0

    except:
        bs.scr.echo(f'Columns not found.')
        return

    for _, geofence in loaded_gpd.iterrows():

        # extract coordiantes from geofence gdf
        lons = geofence.geometry.boundary.xy[0]
        lats = geofence.geometry.boundary.xy[1]

        # convert into a list of lat/lon
        coordinates = [None]*(len(lons)*2)
        coordinates[::2] = lats
        coordinates[1::2] = lons
        
        Geofence(geofence[name_col], coordinates, geofence[top_col], geofence[bottom_col])
    bs.scr.echo(f'Geofences loaded from {filename}.')

@timed_function(dt = 0.5)
def update_intrusions():
    Geofence.detect_inside(bs.traf)
    return

class Geofence(areafilter.Poly):
    ''' BlueSky Geofence class.
    
        This class subclasses Shape, and adds Geofence-specific data and methods.
    '''
    # Keep dicts of geofences by either name or rtree ID
    geo_by_name = dict()
    geo_by_id = dict()
    geo_name2id = dict()

    # Also have a dictionary used for saving and loading geofences
    geo_save_dict = dict()

    # Keep an Rtree of geofences
    geo_tree = index.Index()

    # Keep track of the geofences themselves that aircraft are hitting or intruding in
    # "intrusions" contains aircraft that are currently intruding inside a geofence, and a list
    # of the geofences they are intruding in
    # "hits" contains the geofences that aircraft are about to hit (or are intruding)
    intrusions = dict()
    hits = dict()
    
    # Unique intrusions dictionary: for each aircraft, keep track of the unique intrusions
    # that ever happened.
    unique_intrusions = dict()

    # read edge_geometry.csv as pandas dataframe
    edges_df = pd.read_csv('plugins/edge_geometry.csv')

    # convert geometry text column to shapely geometry
    edges_df['geometry'] = edges_df['geometry'].apply(wkt.loads)

    # create rtree index for each edge
    edges_df_rtree = index.Index()
    edges_df_dict = {}
    for i, row in edges_df.iterrows():
        edges_df_rtree.insert(i, row['geometry'].bounds)
        edges_df_dict[i] = (row['u'], row['v'])

    # initialize numpy array that will contain all nodes currently in the geofence
    edges_in_loiter_geofence = np.array([], dtype='i8,i8')

    def __init__(self, name, coordinates, top=999999, bottom=-999999):
        super().__init__(name, coordinates, top=top, bottom=bottom)
        self.active = True
        #Add info to geofence save dictionary
        geo_dict = dict()
        geo_dict['name'] = name
        geo_dict['coordinates'] = coordinates
        geo_dict['top'] = top
        geo_dict['bottom'] = bottom
        Geofence.geo_save_dict[name] = geo_dict

        # Also add the class instance itself to the other dictionaries
        Geofence.geo_by_name[name] = self
        Geofence.geo_by_id[self.area_id] = self
        Geofence.geo_name2id[name] = self.area_id

        # Insert the geofence in the geofence Rtree
        Geofence.geo_tree.insert(self.area_id, self.bbox)

    def intersects(self, line):
        ''' Check whether given line intersects with this geofence poly. '''
        line_path = Path(line)
        return self.border.intersects_path(line_path)

    @classmethod
    def reset(cls):
        ''' Reset geofence database when simulation is reset. '''
        cls.geo_by_name.clear()
        cls.geo_by_id.clear()
        cls.geo_name2id.clear()
        cls.geo_save_dict.clear()
        cls.geo_tree = index.Index()
        cls.hits.clear()
        cls.intrusions.clear()
        cls.unique_intrusions.clear()

    @classmethod
    def delete(cls, name):
        geo_to_delete = cls.geo_by_name[name]
        cls.geo_by_name.pop(name)
        cls.geo_save_dict.pop(name)
        geo_id = cls.geo_name2id[name]
        cls.geo_tree.delete(geo_id, geo_to_delete.bbox)
        cls.geo_by_id.pop(geo_id)
        cls.geo_name2id.pop(name)

    @classmethod
    def intersecting(cls, coordinates):
        '''Get the geofences that intersect coordinates (either bbox or point).'''
        poly_ids = list(cls.geo_tree.intersection(coordinates))
        return [cls.geo_by_id[id] for id in poly_ids], poly_ids

    @classmethod
    def detect_all(cls, traf, dtlookahead=None):
        if dtlookahead is None:
            dtlookahead = settings.geofence_dtlookahead
        # Reset the hits dict
        cls.hits.clear()
        # Linearly extrapolate current state to prefict future position
        pred_lat, pred_lon = geo.kwikpos(traf.lat, traf.lon, traf.hdg, traf.gs / aero.nm * dtlookahead)
        for idx, line in enumerate(zip(traf.lat, traf.lon, pred_lat, pred_lon)):
            acid = traf.id[idx]
            # First a course detection based on geofence bounding boxes
            potential_hits= areafilter.get_intersecting(*line)
            # Then a fine-grained intersection detection
            hits = []
            for geofence in potential_hits:
                if geofence.intersects(line):
                    hits.append(geofence)
            cls.hits[acid] = hits
        return

    @classmethod
    def detect_inside(cls, traf):
        # Reset the intrusions dict
        #cls.intrusions.clear()
        for idx, point in enumerate(zip(traf.lat, traf.lon, traf.alt)):
            acid = traf.id[idx]
            # First, a course detection based on geofence bounding boxes
            potential_intrusions, geo_ids = cls.intersecting([point[0], point[1]])
            ac_alt = point[2]/aero.ft
            # Then a fine-grained intrusion detection
            #intrusions = []
            for i, geofence in enumerate(potential_intrusions):
                if ac_alt < geofence.top and geofence.checkInside(*point):
                    #intrusions.append(geofence)
                    # Add geofence ID to unique intrusion dictionary
                    if acid not in cls.unique_intrusions:
                        cls.unique_intrusions[acid] = dict()
                    # get geo_name
                    geo_name = cls.geo_by_id[geo_ids[i]].name
                    # Get closest point
                    p1,p2 = nearest_points(geofence.polybound, Point(traf.lat[idx], traf.lon[idx]))
                    # Do kwikdist
                    intrusion = geo.kwikdist(p1.x, p1.y, p2.x, p2.y) * aero.nm
                    # Check the previous intrusion severity
                    if geo_ids[i] in cls.unique_intrusions[acid]:
                        if cls.unique_intrusions[acid][geo_ids[i]][1] < intrusion:
                            cls.unique_intrusions[acid][geo_ids[i]] = [geo_name, intrusion, p2.x, p2.y, bs.sim.simt]
                    else:
                        cls.unique_intrusions[acid][geo_ids[i]] = [geo_name, intrusion, p2.x, p2.y,  bs.sim.simt]
                    
            #cls.intrusions[acid] = intrusions
        bs.traf.geo_intrusions = cls.unique_intrusions
        # print(intrusions)
        return

    @classmethod
    def update_edges_in_loitering_geofences(cls, name, update='add'):
        '''Add/Delete the edges inside a loitering geofence from cls.edges_in_loiter_geofence.'''

        # get geofence from name
        geofence = cls.geo_by_name[name]

        # switch the order odd and even entries of list geofence.bbox for rtree because shapely uses lon,lat
        poly_bounds = (geofence.bbox[1], geofence.bbox[0], geofence.bbox[3], geofence.bbox[2])

        # check the nearest edges to the polygon
        intersecting_rtree = list(cls.edges_df_rtree.intersection(poly_bounds))
        intersecting_edges = np.array([cls.edges_df_dict[i] for i in intersecting_rtree], dtype='i8,i8')

        if update == 'add':
            # concate points inside to class variable nodes_in_geofence but only keep unique values
            cls.edges_in_loiter_geofence = np.unique(np.concatenate((cls.edges_in_loiter_geofence, intersecting_edges)))
        elif update == 'del':
            # delete the edges inside from class variable nodes_in_geofence
            # TODO: potential issue if we remove an edge that is also in another geofence.
            # TODO: perhaps this is not a problem because loitering geofences don't overlap and will be rare
            # TODO: If yes then we might need to start adding an edges_in_loiter_geofence attribute 
            # for each instance of the geofence.
            cls.edges_in_loiter_geofence = np.setdiff1d(cls.edges_in_loiter_geofence, intersecting_edges)
