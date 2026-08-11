create_clock -period 40.000 -name link_clk [get_ports {link_clk}]
create_clock -period 31.250 -name clk_32m  [get_ports {clk_32m}]
