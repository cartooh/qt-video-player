from setuptools import setup, Extension, find_packages
from Cython.Build import cythonize
from numpy import get_include 

ext = Extension("VideoPlayerWidget", sources=["VideoPlayerWidget.pyx"], include_dirs=['.', get_include()])

setup(
    ext_modules=cythonize([ext], language_level="3"),
    packages=find_packages(include=['VideoPlayerWidget'])
)