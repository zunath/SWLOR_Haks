IF NOT EXIST "./output" mkdir output

nwn_erf -f "./output/swlor_2da.hak" -e HAK -c ./swlor_2da 
nwn_erf -f "./output/swlor_add_doors.hak" -e HAK -c ./swlor_add_doors 
nwn_erf -f "./output/swlor_add_loads.hak" -e HAK -c ./swlor_add_loads
nwn_erf -f "./output/swlor_add_skies.hak" -e HAK -c ./swlor_add_skies
nwn_erf -f "./output/swlor_add_tiles1.hak" -e HAK -c ./swlor_add_tiles1
nwn_erf -f "./output/swlor_add_tiles2.hak" -e HAK -c ./swlor_add_tiles2
nwn_erf -f "./output/swlor_core0.hak" -e HAK -c ./swlor_core0
nwn_erf -f "./output/swlor_core1.hak" -e HAK -c ./swlor_core1
nwn_erf -f "./output/swlor_core2.hak" -e HAK -c ./swlor_core2
nwn_erf -f "./output/swlor_core3.hak" -e HAK -c ./swlor_core3
nwn_erf -f "./output/swlor_core4.hak" -e HAK -c ./swlor_core4
nwn_erf -f "./output/swlor_core5.hak" -e HAK -c ./swlor_core5
nwn_erf -f "./output/swlor_core6.hak" -e HAK -c ./swlor_core6
nwn_erf -f "./output/swlor_core7.hak" -e HAK -c ./swlor_core7
nwn_erf -f "./output/swlor_dds.hak" -e HAK -c ./swlor_dds
nwn_erf -f "./output/swlor_dwk.hak" -e HAK -c ./swlor_dwk
nwn_erf -f "./output/swlor_ext_tiles.hak" -e HAK -c ./swlor_ext_tiles
nwn_erf -f "./output/swlor_gui.hak" -e HAK -c ./swlor_gui
nwn_erf -f "./output/swlor_ini.hak" -e HAK -c ./swlor_ini
nwn_erf -f "./output/swlor_itp.hak" -e HAK -c ./swlor_itp
nwn_erf -f "./output/swlor_mdl.hak" -e HAK -c ./swlor_mdl
nwn_erf -f "./output/swlor_mdl_p.hak" -e HAK -c ./swlor_mdl_p
nwn_erf -f "./output/swlor_mtr.hak" -e HAK -c ./swlor_mtr
nwn_erf -f "./output/swlor_plt.hak" -e HAK -c ./swlor_plt
nwn_erf -f "./output/swlor_portraits.hak" -e HAK -c ./swlor_portraits
nwn_erf -f "./output/swlor_pwk.hak" -e HAK -c ./swlor_pwk
nwn_erf -f "./output/swlor_set.hak" -e HAK -c ./swlor_set
nwn_erf -f "./output/swlor_shd.hak" -e HAK -c ./swlor_shd
nwn_erf -f "./output/swlor_ssf.hak" -e HAK -c ./swlor_ssf
nwn_erf -f "./output/swlor_tga.hak" -e HAK -c ./swlor_tga
nwn_erf -f "./output/swlor_tga_ip.hak" -e HAK -c ./swlor_tga_ip
nwn_erf -f "./output/swlor_txi.hak" -e HAK -c ./swlor_txi
nwn_erf -f "./output/swlor_utp.hak" -e HAK -c ./swlor_utp
nwn_erf -f "./output/swlor_wav.hak" -e HAK -c ./swlor_wav
nwn_erf -f "./output/swlor_wok.hak" -e HAK -c ./swlor_wok

xcopy "./swlor_tlk.tlk" "./output/" /F /Y
ECHO All haks built to the output directory.