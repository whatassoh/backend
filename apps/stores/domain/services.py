from django.contrib.gis.geos import Point


def make_store_point(longitude: float, latitude: float) -> Point:
    return Point(longitude, latitude, srid=4326)
