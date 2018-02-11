#!/usr/bin/python
# -*- coding: utf-8 -*-

import gflags
import sys
import json

from ModelGenerator import ModelGenerator
from SBMLParser import SBMLParserEncoder


def main(FLAGS):

    if FLAGS.json_model_input is not None:
        json_model = json.load(open(FLAGS.json_model_input, 'r'))
    else:
        json_model = None

    model_generator = ModelGenerator(FLAGS.sbml_file,
                                     FLAGS.extra_cellular,
                                     FLAGS.periplasm,
                                     FLAGS.cytoplasm,
                                     json_model=json_model,
                                     groups_size=FLAGS.reaction_groups_size)

    if FLAGS.json_model_output:
        json.dump(model_generator.parser, open(FLAGS.json_model_output, 'w+'), cls=SBMLParserEncoder, indent=4)

    model_generator.generate_top()
    model_generator.end_model()


if __name__ == '__main__':

    gflags.DEFINE_string('sbml_file', None, 'The SBML file path to parse', short_name='f')
    gflags.DEFINE_string('extra_cellular', 'e', 'The extra cellular space ID in the SBML file', short_name='e')
    gflags.DEFINE_string('cytoplasm', 'c', 'The cytoplasm space ID in the SBML file', short_name='c')
    gflags.DEFINE_string('periplasm', 'p', 'The periplasm space ID in the SBML file', short_name='p')
    gflags.DEFINE_string('reaction_groups_size', 150, 'The size of the reaction set groups', short_name='s')
    gflags.DEFINE_string('json_model_input', None, 'The exported json model', short_name='i')
    gflags.DEFINE_string('json_model_output', None, 'If not None, it export the parsed sbml model as json to avoid'
                         'reparsing the sbml model in the future. Note: parsing a SBML is a slow process.',
                         short_name='o')

    gflags.MarkFlagAsRequired('sbml_file')
    FLAGS = gflags.FLAGS

    try:
        argv = FLAGS(sys.argv)  # parse flags
    except gflags.FlagsError, e:
        print '%s\nUsage: %s ARGS\n%s' % (e, sys.argv[0], FLAGS)
        sys.exit(1)

    main(FLAGS)
