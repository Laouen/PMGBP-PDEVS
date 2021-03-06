#!/usr/bin/python3
# -*- coding: utf-8 -*-

from .SBMLParser import SBMLParser
from .ModelCodeWriter import ModelCodeWriter
from .XMLParametersWriter import XMLParametersWriter
from .constants import *
from itertools import islice


def chunks(data, SIZE=150):
    it = iter(data)
    for i in range(0, len(data), SIZE):
        yield {k: data[k] for k in islice(it, SIZE)}


class ModelStructure:

    def __init__(self, cid, parser, membranes=None, external_enzyme_sets=None):
        """
        Defines the basic structure of a compartment. A compartment has at least one internal
        reaction set called the bulk (i.e. the compartment internal reactions).

        :param cid: The compartment id.
        :param membranes: A list of all the compartment internal reaction sets less the bulk.
        :param parser: A SBMLParser with the sbml file loaded.
        :param external_enzyme_sets:  A dictionary of all the related external reaction sets
        grouped by their compartments. Optional.
        :type external_enzyme_sets: dict[str, list[str]]
        """

        if membranes is None:
            membranes = []

        if external_enzyme_sets is None:
            external_enzyme_sets = {}

        self.id = cid
        self.space = {}
        self.routing_table = {}

        # Compartment input-membrane mapping
        port_numbers = range(len(membranes))
        self.membrane_eic = {esn: port
                             for esn, port
                             in zip(membranes, port_numbers)}

        # Building routing table, each reaction can be reached using the port
        # that the routing table indicates. Each port communicates the space with
        # a different reaction_set
        #
        # The bulk reaction set is always present but is not a membrane, that is why it is not
        # considered in the input-membrane mapping.
        #
        # Initial ports are for IC (internal reaction sets)
        internal_enzyme_sets = [BULK] + membranes
        port_numbers = range(len(internal_enzyme_sets))
        self.routing_table = {(self.id, esn): port
                              for esn, port
                              in zip(internal_enzyme_sets, port_numbers)}

        # Final ports are for EOC (external reaction sets from other compartments)
        port_number = len(self.routing_table)
        for external_cid, reaction_sets in external_enzyme_sets.items():
            for reaction_set in reaction_sets:
                self.routing_table[(external_cid, reaction_set)] = port_number
                port_number += 1

        # Building enzyme sets
        self.enzyme_sets = {}
        for enzyme_set in internal_enzyme_sets:
            self.enzyme_sets[enzyme_set] = parser.get_enzyme_set(cid, enzyme_set)
 
        # Building space
        reaction_parameters = parser.get_related_reaction_parameters(cid)
        metabolites = {specie: parser.metabolite_amounts[specie]
                       for specie in list(parser.parse_compartments_species()[cid].keys())}
        self.space_parameters = {
            'cid': cid,
            'interval_time': parser.interval_times[cid],
            'metabolites': metabolites,
            'reaction_parameters': reaction_parameters,
            'volume': parser.get_compartment_volume(cid),
            'enzymes': parser.get_enzymes(list(reaction_parameters.keys()))
        }


class ModelGenerator:
    """
    Generates a model structure from a SBML file using the SBMLParser to get the information
    """

    def __init__(self,
                 sbml_file,
                 extra_cellular_id,
                 periplasm_id,
                 cytoplasm_id,
                 model_dir='..',
                 json_model=None,
                 groups_size=150,
                 kon=0.8,
                 koff=0.8,
                 rates='0:0:0:1',
                 enzymes=1000,
                 metabolites=600000):
        """
        Generates the whole model structure and generates a .cpp file with a cadmium model from the
        generated structure

        :param sbml_file: Path to the sbml file to parse
        :param extra_cellular_id: The extra cellular ID, this ID must be one of the compartments
        IDs from the sbml file
        :param periplasm_id: The periplasm ID, this ID must be one of the compartments IDs from
        the sbml file
        :param cytoplasm_id: The cytoplasm ID, this ID must be one of the compartments IDs from
        the sbml file
        :param model_dir: Path to the directory where the generated model will be stored
        :param json_model: The parser exported as json. Optional, used to avoid re parsing
        :param groups_size: The size of the reaction set groups
        """

        self.groups_size = groups_size
        self.parameter_writer = XMLParametersWriter(model_dir=model_dir)
        self.coder = ModelCodeWriter(model_dir=model_dir)
        self.parser = SBMLParser(sbml_file,
                                 extra_cellular_id,
                                 periplasm_id,
                                 cytoplasm_id,
                                 json_model=json_model,
                                 default_konSTP=kon,
                                 default_konPTS=kon,
                                 default_koffSTP=koff,
                                 default_koffPTS=koff,
                                 default_metabolite_amount=metabolites,
                                 default_enzyme_amount=enzymes,
                                 default_rate=rates,
                                 default_reject_rate=rates,
                                 default_interval_time=rates)

        special_compartment_ids = [extra_cellular_id, periplasm_id, cytoplasm_id]

        c_external_enzyme_sets = {cid: [MEMBRANE]
                                    for cid in self.parser.get_compartments()
                                    if cid not in special_compartment_ids}
        c_external_enzyme_sets[periplasm_id] = [INNER, TRANS]

        e_external_enzyme_sets = {periplasm_id: [OUTER, TRANS]}

        # Compartment model structures
        self.periplasm = ModelStructure(periplasm_id,
                                        self.parser,
                                        membranes=[OUTER, INNER, TRANS])

        self.extra_cellular = ModelStructure(extra_cellular_id,
                                             self.parser,
                                             external_enzyme_sets=e_external_enzyme_sets)

        self.cytoplasm = ModelStructure(cytoplasm_id,
                                        self.parser,
                                        external_enzyme_sets=c_external_enzyme_sets)

        self.organelles = {comp_id: ModelStructure(comp_id, self.parser, [MEMBRANE])
                           for comp_id in self.parser.get_compartments()
                           if comp_id not in special_compartment_ids}

    def generate_enzyme_sets(self, compartment):
        cid = compartment.id
        return {(cid, esn): self.generate_enzyme_set(cid, esn, enzyme_set)
                for esn, enzyme_set in compartment.enzyme_sets.items()}

    def generate_enzyme_set(self, cid, esn, enzyme_set):
        enzyme_set_id = '_'.join([cid, esn])

        # Enzyme sets are separated in groups, this is because the C++ problem compiling large tuples
        # Currently the maximum group amount is 150 and each group can hold 150 enzymes, thus, each compartment
        # can have a maximum of 150 * 150 = 22500 enzymes.
        groups = chunks(enzyme_set, SIZE=self.groups_size)
        groups_enzyme_ids = []
        routing_table = {}
        
        # Routers xml parameters
        port = 0
        for group in groups:
            enzyme_ids = list(group.keys())
            groups_enzyme_ids.append(enzyme_ids)

            group_id = '_'.join([cid, esn, str(port)])
            routing_table.update({eid: port for eid in enzyme_ids})
            port += 1

            port_numbers = range(len(enzyme_ids))
            group_routing_table = {eid: port_number for eid, port_number in zip(enzyme_ids, port_numbers)}
            self.parameter_writer.add_router(group_id, group_routing_table)

        self.parameter_writer.add_router(enzyme_set_id, routing_table)

        return self.coder.write_enzyme_set(cid, esn, groups_enzyme_ids)

    def generate_organelle_compartment(self, compartment):

        enzyme_sets = self.generate_enzyme_sets(compartment)

        self.parameter_writer.add_space(compartment.id,
                                        compartment.space_parameters,
                                        compartment.routing_table)
        
        space = self.coder.write_space_atomic_model(compartment.id,
                                                    [compartment.id],
                                                    len(enzyme_sets),
                                                    1,
                                                    PRODUCT_MESSAGE_TYPE,
                                                    REACTANT_MESSAGE_TYPE,
                                                    INFORMATION_MESSAGE_TYPE)

        sub_models = [space] + [es_model_name for (es_model_name, _, _) in enzyme_sets.values()]

        ic = []
        for es_address, port_number in compartment.routing_table.items():
            # Organelle spaces do not send metabolites to other compartments without passing through
            # their membranes, that is the assert.
            assert es_address[0] == compartment.id
            es_model_name = enzyme_sets[es_address][0]
            ic += [
                (space, 'pmgbp::structs::'+space+'::'+space, port_number, es_model_name, 'pmgbp::models::enzyme', 0),
                (es_model_name, 'pmgbp::models::enzyme', "0_product", space, 'pmgbp::structs::'+space+'::'+space, "0_product"),
                (es_model_name, 'pmgbp::models::enzyme', "0_information", space, 'pmgbp::structs::'+space+'::'+space, "0_information")
            ]

        # If the output port amount is equal to 1, then the model only sends messages to the space
        # and there is no communication with the extra cellular space, thus, it must not be linked
        # in the EOC.
        # Because the port 0 of each reaction set goes to the space, the output ports range is
        # [1, ... , output_port_amount)
        out_port_numbers = []
        out_ports = []
        eoc = []
        for (es_model_name, _, output_port_amount) in enzyme_sets.values():
            for port_number in range(1, output_port_amount):
                eoc.append((es_model_name, 'pmgbp::models::enzyme', str(port_number) + "_product", str(port_number) + "_product"))
                eoc.append((es_model_name, 'pmgbp::models::enzyme', str(port_number) + "_information", str(port_number) + "_information"))
                if port_number not in out_port_numbers:
                    out_port_numbers.append(port_number)
                    out_ports.append((str(port_number) + "_product", PRODUCT_MESSAGE_TYPE, 'out'))
                    out_ports.append((str(port_number) + "_information", INFORMATION_MESSAGE_TYPE, 'out'))

        in_ports = []
        eic = []
        for es_name, port_number in compartment.membrane_eic.items():
            es_model_name = enzyme_sets[(compartment.id, es_name)][0]
            eic.append((es_model_name, 'pmgbp::models::enzyme', 0, port_number))
            in_ports.append((port_number, REACTANT_MESSAGE_TYPE, 'in'))
            # TODO: Check if this link shouldn't be removed
            # in_ports.append((port_number, REACTANT_MESSAGE_TYPE, 'in'))

        return self.coder.write_coupled_model(compartment.id,
                                              sub_models,
                                              out_ports + in_ports,
                                              eic,
                                              eoc,
                                              ic)

    # Bulk compartments are cytoplasm and extracellular space, they represent the space where the
    # cell and the organelles live
    def generate_bulk_compartment(self, compartment):
        cid = compartment.id
        enzyme_sets = self.generate_enzyme_sets(compartment)
        assert len(enzyme_sets) == 1  # bulk compartments have no membranes

        bulk = enzyme_sets[(cid, BULK)][0]

        self.parameter_writer.add_space(cid, compartment.space_parameters, compartment.routing_table)

        # port numbers starts in zero -> port amount = max{port numbers} + 1.
        output_port_amount = max(compartment.routing_table.values()) + 1
        space = self.coder.write_space_atomic_model(cid,
                                                    [cid],
                                                    output_port_amount,
                                                    1,
                                                    PRODUCT_MESSAGE_TYPE,
                                                    REACTANT_MESSAGE_TYPE,
                                                    INFORMATION_MESSAGE_TYPE)

        sub_models = [space, bulk]

        bulk_port_number = compartment.routing_table[(cid, BULK)]
        ic = [
            (space, 'pmgbp::structs::'+space+'::'+space, bulk_port_number, bulk, 'pmgbp::models::enzyme', 0), 
            (bulk, 'pmgbp::models::enzyme', "0_product", space, 'pmgbp::structs::'+space+'::'+space, "0_product"),
            (bulk, 'pmgbp::models::enzyme', "0_information", space, 'pmgbp::structs::'+space+'::'+space, "0_information")
        ]

        out_ports = []
        eoc = []
        for (es_cid, _), port_number in compartment.routing_table.items():
            if es_cid != cid:
                eoc.append((space, 'pmgbp::structs::'+space+'::'+space, port_number, port_number))
                out_ports.append((port_number, REACTANT_MESSAGE_TYPE, 'out'))

        in_ports = [
            ("0_product", PRODUCT_MESSAGE_TYPE, 'in'),
            ("0_information", INFORMATION_MESSAGE_TYPE, 'in')
        ]

        eic = [
            (space, 'pmgbp::structs::'+space+'::'+space, "0_product", "0_product"),
            (space, 'pmgbp::structs::'+space+'::'+space, "0_information", "0_information")
        ]

        return self.coder.write_coupled_model(cid,
                                              sub_models,
                                              in_ports + out_ports,
                                              eic,
                                              eoc,
                                              ic)

    def generate_top(self, top='cell'):

        # add all the reaction parameters
        for rid, parameters in self.parser.reactions.items():
            self.parameter_writer.add_reaction(rid, parameters)

        # add all the enzyme parameters
        for eid, parameters in self.parser.enzymes.items():
            self.parameter_writer.add_enzyme(eid, parameters)


        cytoplasm_model = self.generate_bulk_compartment(self.cytoplasm)[0]
        extra_cellular_model = self.generate_bulk_compartment(self.extra_cellular)[0]
        periplasm_model = self.generate_organelle_compartment(self.periplasm)[0]

        sub_models = [cytoplasm_model, extra_cellular_model, periplasm_model]

        organelle_models = {}
        for cid, model_structure in self.organelles.items():
            organelle_models[cid] = self.generate_organelle_compartment(model_structure)
            sub_models.append(organelle_models[cid][0])  # append model name

        ic = self.generate_top_bulk_ic(cytoplasm_model,
                                       self.cytoplasm.id,
                                       self.cytoplasm.routing_table,
                                       periplasm_model)

        ic += self.generate_top_bulk_ic(extra_cellular_model,
                                        self.extra_cellular.id,
                                        self.extra_cellular.routing_table,
                                        periplasm_model)

        for cid, (model_name, _, output_port_amount) in organelle_models.items():
            for esn, es_port_number in self.organelles[cid].membrane_eic.items():
                c_port_number = self.cytoplasm.routing_table[(cid, esn)]
                ic.append((cytoplasm_model, c_port_number, model_name, es_port_number))
                # *1) organelle output port 1 always goes to cytoplasm
                ic.append((model_name, model_name, "1_product", cytoplasm_model, cytoplasm_model, "0_product"))
                ic.append((model_name, model_name, "1_information", cytoplasm_model, cytoplasm_model, "0_information"))

                if output_port_amount > 1:
                    e_port_number = self.cytoplasm.routing_table[(cid, esn)]
                    ic.append((extra_cellular_model, extra_cellular_model, e_port_number, model_name, model_name, es_port_number))
                    # *1) organelle output port 2 always goes to extracellular
                    ic.append((model_name, model_name, "2_product", extra_cellular_model, extra_cellular_model, "0_product"))
                    ic.append((model_name, model_name, "2_information", extra_cellular_model, extra_cellular_model, "0_information"))

        self.parameter_writer.save_xml()
        cell_coupled = self.coder.write_coupled_model(top, sub_models, [], [], [], ic)

        return cell_coupled

    def generate_top_bulk_ic(self, bulk_model, bulk_cid, bulk_routing_table, periplasm_model):
        ic = []

        # this is the same logic as *1)
        periplasm_oport_number = 1 if bulk_cid == self.cytoplasm.id else 2
        ic.append((periplasm_model, periplasm_model, str(periplasm_oport_number) + "_product", bulk_model, bulk_model, "0_product"))
        ic.append((periplasm_model, periplasm_model, str(periplasm_oport_number) + "_information", bulk_model, bulk_model, "0_information"))

        for (cid, esn), c_port_number in bulk_routing_table.items():
            if cid == bulk_cid:
                continue

            if cid == self.periplasm.id:
                periplasm_port_number = self.periplasm.membrane_eic[esn]
                ic.append((bulk_model, bulk_model, c_port_number, periplasm_model, periplasm_model, periplasm_port_number))
        return ic

    def end_model(self):
        self.coder.end_model()
