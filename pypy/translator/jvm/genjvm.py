"""
Backend for the JVM.
"""

import os, os.path, subprocess, sys

import py
from pypy.tool.udir import udir
from pypy.translator.translator import TranslationContext
from pypy.translator.oosupport.genoo import GenOO

from pypy.translator.jvm.generator import JasminGenerator
from pypy.translator.jvm.option import getoption
from pypy.translator.jvm.database import Database
from pypy.translator.jvm.log import log
from pypy.translator.jvm.node import EntryPoint, Function
from pypy.translator.jvm.opcodes import opcodes

class JvmError(Exception):
    """ Indicates an error occurred in JVM backend """

    def pretty_print(self):
        print str(self)
    pass

class JvmSubprogramError(JvmError):
    """ Indicates an error occurred running some program """
    def __init__(self, res, args, stdout, stderr):
        self.res = res
        self.args = args
        self.stdout = stdout
        self.stderr = stderr

    def __str__(self):
        return "Error code %d running %s" % (self.res, repr(self.args))
        
    def pretty_print(self):
        JvmError.pretty_print(self)
        print "vvv Stdout vvv\n"
        print self.stdout
        print "vvv Stderr vvv\n"
        print self.stderr
        
    pass

class JvmGeneratedSource(object):
    
    """
    An object which represents the generated sources. Contains methods
    to find out where they are located, to compile them, and to execute
    them.

    For those interested in the location of the files, the following
    attributes exist:
    tmpdir --- root directory from which all files can be found (py.path obj)
    javadir --- the directory containing *.java (py.path obj)
    classdir --- the directory where *.class will be generated (py.path obj)
    package --- a string with the name of the package (i.e., 'java.util')

    The following attributes also exist to find the state of the sources:
    compiled --- True once the sources have been compiled successfully
    """

    def __init__(self, tmpdir, package):
        """
        'tmpdir' --- the base directory where the sources are located
        'package' --- the package the sources are in; if package is pypy.jvm,
        then we expect to find the sources in $tmpdir/pypy/jvm
        """
        self.tmpdir = tmpdir
        self.package = package
        self.compiled = False

        # Compute directory where .java files are
        self.javadir = self.tmpdir
        for subpkg in package.split('.'):
            self.javadir = self.javadir.join(subpkg)

        # Compute directory where .class files should go
        self.classdir = self.javadir

    def _invoke(self, args, allow_stderr):
        subp = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = subp.communicate()
        res = subp.wait()
        if res or (not allow_stderr and stderr):
            raise JvmSubprogramError(res, args, stdout, stderr)
        return stdout

    def compile(self):
        """
        Compiles the .java sources into .class files, ready for execution.
        """
        javacmd = [getoption('jasmin'), '-d', str(self.javadir)]
        pypydir = self.javadir.join("pypy") # HACK; should do recursive
        javafiles = [str(f) for f in pypydir.listdir()
                     if str(f).endswith('.j')]

        for javafile in javafiles:
            print "Invoking jasmin on %s" % javafile
            self._invoke(javacmd+[javafile], False)

        # HACK: compile the Java helper class.  Should eventually
        # use rte.py
        sl = __file__.rindex('/')
        javasrc = __file__[:sl]+"/src/PyPy.java"
        self._invoke([getoption('javac'),
                      '-nowarn',
                      '-d', str(self.javadir),
                      javasrc],
                     True)
                           
        self.compiled = True

    def execute(self, args):
        """
        Executes the compiled sources in a separate process.  Returns the
        output as a string.  The 'args' are provided as arguments,
        and will be converted to strings.
        """
        assert self.compiled
        strargs = [str(a) for a in args]
        cmd = [getoption('java'),
               '-cp',
               str(self.javadir),
               self.package+".Main"] + strargs
        return self._invoke(cmd, True)
        
def generate_source_for_function(func, annotation):
    
    """
    Given a Python function and some hints about its argument types,
    generates JVM sources that call it and print the result.  Returns
    the JvmGeneratedSource object.
    """
    
    if hasattr(func, 'im_func'):
        func = func.im_func
    t = TranslationContext()
    ann = t.buildannotator()
    ann.build_types(func, annotation)
    t.buildrtyper(type_system="ootype").specialize()
    main_graph = t.graphs[0]
    if getoption('view'): t.view()
    if getoption('wd'): tmpdir = py.path.local('.')
    else: tmpdir = udir
    jvm = GenJvm(tmpdir, t, EntryPoint(main_graph, True, True))
    return jvm.generate_source()

def detect_missing_support_programs():
    def check(exechelper):
        try:
            py.path.local.sysfind(exechelper)
        except py.error.ENOENT:
            py.test.skip("%s is not on your path" % exechelper)
    check(getoption('jasmin'))
    check(getoption('javac'))
    check(getoption('java'))

class GenJvm(GenOO):

    """ Master object which guides the JVM backend along.  To use,
    create with appropriate parameters and then invoke
    generate_source().  *You can not use one of these objects more than
    once.* """

    TypeSystem = lambda X, db: db # TypeSystem and Database are the same object 
    Function = Function
    Database = Database
    opcodes = opcodes
    log = log
    
    def __init__(self, tmpdir, translator, entrypoint):
        """
        'tmpdir' --- where the generated files will go.  In fact, we will
        put our binaries into the directory pypy/jvm
        'translator' --- a TranslationContext object
        'entrypoint' --- if supplied, an object with a render method
        """
        GenOO.__init__(self, tmpdir, translator, entrypoint)
        self.jvmsrc = JvmGeneratedSource(tmpdir, getoption('package'))

    def generate_source(self):
        """ Creates the sources, and returns a JvmGeneratedSource object
        for manipulating them """
        GenOO.generate_source(self)
        return self.jvmsrc

    def create_assembler(self):
        """ Creates and returns a Generator object according to the
        configuration.  Right now, however, there is only one kind of
        generator: JasminGenerator """
        return JasminGenerator(
            self.db, self.jvmsrc.javadir, self.jvmsrc.package)
        
        
