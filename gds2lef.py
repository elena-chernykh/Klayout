# Some information:
# Layer names are stored in dictionary 'names' {'layer_number': 'name'}
# Function 'split_poly' represents a rectilinear polygon as a collection of rectangles
# Pin directions are stored in dictionary 'directions' {'cell_name': {'pin_name' :'direction'}}

import sys
from collections import defaultdict
import re
import pya
import xml.etree.ElementTree as ET
from liberty.parser import parse_liberty
from subprocess import Popen, PIPE
import shlex
import json

def parsing_lyp(url):
    # Layer names only
    tree = ET.parse(url)
    root = tree.getroot()
    layer_names = dict()
    for properties in root:
        layer_info = properties.find('name').text.split(' - ')
        layer_names[layer_info[1]] = layer_info[0]
    return layer_names


def parsing_lyt(url):
    tree = ET.parse(url)
    root = tree.getroot()
      
    # layer_names is {'layer_num' : 'layer_name'}
    layer_names = dict()
    # layer_labels is {'layer_name' : 'layer_with_text_num'}
    layer_labels = dict()
    
    # Routing layers
    routing_suffix = root.find('reader-options').find('lefdef').find('routing-suffix-string').text
    routing_datatype = root.find('reader-options').find('lefdef').find('routing-datatype-string').text
    label_suffix = root.find('reader-options').find('lefdef').find('labels-suffix').text
    label_datatype = root.find('reader-options').find('lefdef').find('labels-datatype').text
    
    # Boundary layer
    boundary_layer_name  = root.find('reader-options').find('lefdef').find('cell-outline-layer').text
    
    layer_map = root.find('reader-options').find('lefdef').find('layer-map').text[10:-2].split('\';\'')
    boundary_layer = None

    for layer in layer_map:
        layer_curr = layer.split(' : ')
        if routing_suffix is not None and re.match('.{1,}' + f'{routing_suffix}', layer_curr[0]) is not None and layer_curr[1].split('/')[1] == routing_datatype:
            layer_names[layer_curr[1]] = layer_curr[0][:-len(routing_suffix)]
        elif re.match('.{1,}' + f'{label_suffix}', layer_curr[0]) is not None and layer_curr[1].split('/')[1] == label_datatype:
            layer_labels[layer_curr[0][:-len(label_suffix)]] = layer_curr[1]
        elif layer_curr[0] == boundary_layer_name:
            boundary_layer = (layer_curr[1], layer_curr[0])

    return boundary_layer, layer_names, layer_labels


def parsing_lib(url):
    # Read and parse a library
    library = parse_liberty(open(url).read())

    directions = dict()

    # Loop through all cells
    for cell_group in library.get_groups('cell'):
        cell_name = cell_group.args[0]
        try:
            directions[cell_name] = dict()
            directions[cell_name]['VDD'] = 'OUTPUT'
            directions[cell_name]['GND'] = 'INPUT'
        except TypeError:
            directions[str(cell_name)[1:-1]] = dict()
            directions[str(cell_name)[1:-1]]['VDD'] = 'OUTPUT'
            directions[str(cell_name)[1:-1]]['GND'] = 'INPUT'

        # Loop through all pins of the cell
        for pin_group in cell_group.get_groups('pin'):
            pin_name = pin_group.args[0]
            try:
                directions[cell_name][pin_name] = pin_group['direction'].upper()
            except TypeError:
                directions[str(cell_name)[1:-1]][str(pin_name)[1:-1]] = str(pin_group['direction'])[1:-1].upper()
            except AttributeError:
                directions[str(cell_name)[1:-1]][str(pin_name)[1:-1]] = str(pin_group['direction'])[1:-1].upper()
    return directions


def parsing_verilog(url):
    cmd = 'yosys ' + f'{url}' + ' -p json'
    args = shlex.split(cmd)
    result = Popen(args, stdout = PIPE)
    output_str = result.communicate()[0].decode("utf-8")

    # Getting json part from output
    start_number = output_str.find('\"creator\"') - 5 # considering '{\n  '
    end_number = output_str.rfind('}')
    result_json = output_str[start_number:end_number+1]
    cells_info = json.loads(result_json)

    # Writing to dictionary {'cell_name': {'pin_name' :'direction'}}
    directions = dict()
    for cell in cells_info['modules']:
        directions[cell] = dict()
        ports = cells_info['modules'][cell]['ports']
        for port_name, port_characters in ports.items():
            for i in range(len(port_characters['bits'])):
                if len(port_characters['bits']) > 1:
                    directions[cell][port_name + '[' + str(i) + ']'] = port_characters['direction']
                else:
                    directions[cell][port_name] = port_characters['direction']
    return directions



# Splitting polygons
def split_poly(poly):
    bad_polygons = []
    boxes = []
    polygons = [poly]
    while len(polygons) > 0:
        last_polygon = polygons.pop()
        new_polygons = last_polygon.split()
        if last_polygon == new_polygons[0]:
            bad_polygons.append(last_polygon)
        else:
            for polygon in new_polygons:
                if polygon.is_box():
                    boxes.append(polygon.bbox())
                else:
                    polygons.append(polygon)
    return [boxes, bad_polygons]


# Writing info about cell to lef-file 
def write_to_lef(cell_name, size, pins_info, obstruction):
    lef_file.write('\n' + 'MACRO ' + cell_name + '\n')
    lef_file.write('  CLASS CORE' + ' ;\n')
    lef_file.write('  ORIGIN 0 0' + ' ;\n')
    lef_file.write('  SIZE ' + size + ' ;\n')
    lef_file.write('  SYMMETRY X Y R90' + ' ;\n')
    lef_file.write('  SITE CoreSite' + ' ;\n')

    for pin in pins_info:
        lef_file.write('\n' + '  PIN ' + pin + '\n')
        if cell_name in directions:
            lef_file.write('    DIRECTION ' + directions[cell_name][pin] + ' ;'+'\n')
        elif pin == 'VDD':
            lef_file.write('    DIRECTION OUTPUT' + ' ;'+'\n')
        elif pin == 'GND':
            lef_file.write('    DIRECTION INPUT' + ' ;'+'\n')
        
        if pin == 'VDD':
            lef_file.write('    USE POWER ;' + '\n')
        elif pin == 'GND':
            lef_file.write('    USE GROUND ;' + '\n')
        else:
            lef_file.write('    USE SIGNAL ;' + '\n')
        for metal in pins_info[pin]:
            lef_file.write('    PORT'+ '\n')
            lef_file.write('      LAYER '+ metal + ' ;' + '\n')
            for rect in pins_info[pin][metal]:
                if not (rect.right - rect.left < 50 or rect.top - rect.bottom < 50): 
                    rect_coords = list(map(lambda x: format(round(x/1000, 4), '.4f'), [rect.left, rect.bottom, rect.right, rect.top]))
                    lef_file.write('        RECT ')
                    for coord in rect_coords:
                        lef_file.write(coord + ' ')
                    lef_file.write(';\n')
            lef_file.write('    END' + '\n')

        lef_file.write('  END ' + pin +'\n')

    lef_file.write('  OBS\n')
    for metal in obstruction:
        lef_file.write('    LAYER ' + metal + ' ;' + '\n')
        lef_file.write('        RECT ')
        rect = obstruction[metal]
        rect_coords = list(map(lambda x: format(round(x/1000, 4), '.4f'), [rect.left, rect.bottom, rect.right, rect.top]))
        for coord in rect_coords:
            lef_file.write(coord + ' ')
        lef_file.write(';\n')
        list(map(lambda x: format(round(x/1000, 4), '.4f'), [rect.left, rect.bottom, rect.right, rect.top]))
    lef_file.write('  END\n')
    lef_file.write('END ' + cell_name + '\n')


input_command = list(sys.argv)

# python or klayout
if input_command[0][-7:] != 'klayout':
    # gds file, lyp/lyt file, verilog/lib file, generated lef file
    gds_file, lyp_lyt_file, verilog_lib_file, new_lef_file = input_command[1:]

else:
    print(
    """
    Input GDS: {gds_file}
    Input lyp/lyt: {lyp_lyt_file}
    Input verilog/lib: {verilog_lib_file}
    Output LEF file: {new_lef_file}
    """.format(gds_file = gds_file,  lyp_lyt_file = lyp_lyt_file, verilog_lib_file = verilog_lib_file, new_lef_file = new_lef_file)
    )

if lyp_lyt_file[-3:] == 'lyp':
    names = parsing_lyp(lyp_lyt_file)
    labels = {}
else:
    bound_layer, names, labels = parsing_lyt(lyp_lyt_file)

if verilog_lib_file[-1] == 'v':
    directions = parsing_verilog(verilog_lib_file)
else:
    directions = parsing_lib(verilog_lib_file)
    

# Generation of new lef-file
lef_file = open(new_lef_file, 'w')
lef_file.write('VERSION 5.6 ;' + '\n')
lef_file.write('BUSBITCHARS \"[]\" ;' + '\n')  
lef_file.write('DIVIDERCHAR \"/\" ;' + '\n')

# Create KLayout object
KLAYOUT = pya.Layout()

# Read GDS file
KLAYOUT.read(gds_file)

# Read cells from GDS file
for top_cell_read in KLAYOUT.top_cells():
    if True:
        cell_info = defaultdict(dict)
        obs = defaultdict(dict)
        size = 'no size'
        all_cell_region = pya.Region()
        for layer_id in KLAYOUT.layer_indexes():
            all_cell_region += pya.Region(top_cell_read.shapes(layer_id))

            layer_num = KLAYOUT.get_info(layer_id).to_s()
            if layer_num in names:
                layer_name = names[layer_num]  # Metal layers only
            
                splitted_polygons = []
                layer_region = pya.Region(top_cell_read.shapes(layer_id))
                if layer_region.area() > 0:
                    obs[layer_name] = layer_region.bbox()

            # Splitting polynons in layer region            
                for region in layer_region.merge().each():
                    if region.is_box():
                        splitted_polygons.append([region.bbox()])
                    else:
                        region = region.to_simple_polygon()
                        rectangles = split_poly(region)
                        splitted_polygons.append(rectangles[0])

                # Pin names in current layer
                text_layer_info = pya.LayerInfo()
                curr_id = KLAYOUT.find_layer(text_layer_info.from_string(labels[layer_name]))
                if curr_id:
                    pin_name_shapes = top_cell_read.shapes(curr_id)
                    for s in pin_name_shapes.each():
                        check = False
                        for polygon in splitted_polygons:
                            for box in polygon:
                                if box.contains(s.text.x, s.text.y):
                                    cell_info[s.text.string][layer_name] = polygon
                                    check = True
                                    break
                            if check:
                                break
            if bound_layer is not None and layer_num == bound_layer[0]:
                for s in top_cell_read.shapes(layer_id).each():
                    size = str(format(round(s.box.width()/1000, 2), '.2f')) + ' BY ' + str(format(round(s.box.height()/1000, 2), '.2f'))

        if size == 'no size':
            x_size = all_cell_region.bbox().width()
            y_size = all_cell_region.bbox().height()
            size = str(format(round(x_size/1000, 2), '.2f')) + ' BY ' + str(format(round(y_size/1000, 2), '.2f'))
            #print(size)
        write_to_lef(top_cell_read.name, size, cell_info, obs)

lef_file.write('\n' + 'END LIBRARY')
lef_file.close() 