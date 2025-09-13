#!/bin/bash

get_target_dir() {
    local file="$1"
    local basename=$(basename "$file")
    
    if [[ $basename =~ _belt ]]; then
        echo "swlor2_pt_belt"
    elif [[ $basename =~ _bicepl|_bicepL ]]; then
        echo "swlor2_pt_bicepl"
    elif [[ $basename =~ _bicepr|_bicepR ]]; then
        echo "swlor2_pt_bicepr"
    elif [[ $basename =~ _chest ]]; then
        echo "swlor2_pt_chest"
    elif [[ $basename =~ _cloak ]]; then
        echo "swlor2_pt_cloak"
    elif [[ $basename =~ _footl|_footL ]]; then
        echo "swlor2_pt_footl"
    elif [[ $basename =~ _footr|_footR ]]; then
        echo "swlor2_pt_footr"
    elif [[ $basename =~ _forel|_foreL ]]; then
        echo "swlor2_pt_forel"
    elif [[ $basename =~ _forer|_foreR ]]; then
        echo "swlor2_pt_forer"
    elif [[ $basename =~ _handl|_handL ]]; then
        echo "swlor2_pt_handl"
    elif [[ $basename =~ _handr|_handR ]]; then
        echo "swlor2_pt_handr"
    elif [[ $basename =~ _head ]]; then
        echo "swlor2_pt_head"
    elif [[ $basename =~ _legl|_legL ]]; then
        echo "swlor2_pt_legl"
    elif [[ $basename =~ _legr|_legR ]]; then
        echo "swlor2_pt_legr"
    elif [[ $basename =~ _neck ]]; then
        echo "swlor2_pt_neck"
    elif [[ $basename =~ _pelvis ]]; then
        echo "swlor2_pt_pelvis"
    elif [[ $basename =~ _robe ]]; then
        echo "swlor2_pt_robe"
    elif [[ $basename =~ _shinl|_shinL ]]; then
        echo "swlor2_pt_shinl"
    elif [[ $basename =~ _shinr|_shinR ]]; then
        echo "swlor2_pt_shinr"
    elif [[ $basename =~ _shol|_shoL ]]; then
        echo "swlor2_pt_shol"
    elif [[ $basename =~ _shor|_shoR ]]; then
        echo "swlor2_pt_shor"
    else
        echo ""
    fi
}

echo "Moving PLT files..."
plt_count=0
find swlor2_plt -name "*.plt" | grep -E "_(belt|bicepl|bicepL|bicepr|bicepR|chest|cloak|footl|footL|footr|footR|forel|foreL|forer|foreR|handl|handL|handr|handR|head|legl|legL|legr|legR|neck|pelvis|robe|shinl|shinL|shinr|shinR|shol|shoL|shor|shoR)" | while IFS= read -r file; do
    target_dir=$(get_target_dir "$file")
    if [[ -n "$target_dir" ]]; then
        if [[ -d "$target_dir" ]]; then
            echo "Moving $(basename "$file") to $target_dir"
            mv "$file" "$target_dir/"
            ((plt_count++))
            if (( plt_count % 100 == 0 )); then
                echo "Processed $plt_count PLT files..."
            fi
        fi
    fi
done

echo "Moving TXI files..."
txi_count=0
find swlor2_txi -name "*.txi" | grep -E "_(belt|bicepl|bicepL|bicepr|bicepR|chest|cloak|footl|footL|footr|footR|forel|foreL|forer|foreR|handl|handL|handr|handR|head|legl|legL|legr|legR|neck|pelvis|robe|shinl|shinL|shinr|shinR|shol|shoL|shor|shoR)" | while IFS= read -r file; do
    target_dir=$(get_target_dir "$file")
    if [[ -n "$target_dir" ]]; then
        if [[ -d "$target_dir" ]]; then
            echo "Moving $(basename "$file") to $target_dir"
            mv "$file" "$target_dir/"
            ((txi_count++))
        fi
    fi
done

echo "Moving PWK files..."
pwk_count=0
find swlor2_dwk swlor2_pwk swlor2_wok -name "*.pwk" -o -name "*.dwk" -o -name "*.wok" 2>/dev/null | grep -E "_(belt|bicepl|bicepL|bicepr|bicepR|chest|cloak|footl|footL|footr|footR|forel|foreL|forer|foreR|handl|handL|handr|handR|head|legl|legL|legr|legR|neck|pelvis|robe|shinl|shinL|shinr|shinR|shol|shoL|shor|shoR)" | while IFS= read -r file; do
    target_dir=$(get_target_dir "$file")
    if [[ -n "$target_dir" ]]; then
        if [[ -d "$target_dir" ]]; then
            echo "Moving $(basename "$file") to $target_dir"
            mv "$file" "$target_dir/"
            ((pwk_count++))
        fi
    fi
done

echo "Additional file movement complete!"
echo "PLT files moved: $plt_count"
echo "TXI files moved: $txi_count"
echo "Walkmesh files moved: $pwk_count"