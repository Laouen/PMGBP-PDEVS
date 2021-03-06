/***************************** ports for model {model_name} ***************************************/

namespace pmgbp {{
namespace structs {{
namespace {model_name} {{

template<class OUTPUT_TYPE, class INPUT_TYPE>
struct ports {{

    {output_ports_definitions}

    {input_ports_definitions}

    using output_type=pmgbp::types::{product_type};
    using input_type=pmgbp::types::{reactant_type};

    using input_ports=std::tuple<{input_port_names}>;
    using output_ports=std::tuple<{output_port_names}>;
}};


}}
}}
}}

using {model_name}_ports = pmgbp::structs::{model_name}::ports<pmgbp::types::{output_type},pmgbp::types::{input_type}>;

template<typename TIME>
using {model_name}_definition = pmgbp::models::{model_class}<{model_name}_ports, TIME>;

/**************************************************************************************************/