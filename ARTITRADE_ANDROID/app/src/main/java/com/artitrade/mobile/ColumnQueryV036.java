package com.artitrade.mobile;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/** A reusable, Android-independent column reorder/sort/filter model.
 * Never mutates original trade or research rows; only a presentation list. */
final class ColumnQueryV036 {
    final ArrayList<String> order=new ArrayList<>();
    final Set<String> hidden=new HashSet<>();
    final Map<String,String> filters=new HashMap<>();
    String sortColumn="";
    boolean descending=false;

    ColumnQueryV036(String... columns){
        for(String c:columns)if(c!=null&&!c.isEmpty()&&!order.contains(c))order.add(c);
    }
    List<String> columns(){return Collections.unmodifiableList(order);}
    List<String> visibleColumns(){
        ArrayList<String> visible=new ArrayList<>();
        for(String c:order)if(!hidden.contains(c))visible.add(c);
        return visible;
    }
    void move(String key,int direction){
        int ix=order.indexOf(key);
        if(ix<0||direction==0)return;
        int target=Math.max(0,Math.min(order.size()-1,ix+(direction<0?-1:1)));
        if(ix==target)return;
        Collections.swap(order,ix,target);
    }
    void hide(String key,boolean hide){
        if(!order.contains(key))return;
        if(hide&&visibleColumns().size()<=1)return;
        if(hide)hidden.add(key);else hidden.remove(key);
    }
    void sort(String key){
        if(!order.contains(key))return;
        if(sortColumn.equals(key))descending=!descending;
        else{sortColumn=key;descending=false;}
    }
    void setFilter(String key,String raw){
        if(!order.contains(key))return;
        String filter=raw==null?"":raw.trim();
        if(filter.isEmpty())filters.remove(key);else filters.put(key,filter);
    }
    boolean matches(Map<String,String> row){
        for(Map.Entry<String,String> f:filters.entrySet()){
            String left=str(row.get(f.getKey()));
            String expected=f.getValue().trim();
            if(expected.startsWith("=")){
                if(!left.equalsIgnoreCase(expected.substring(1).trim()))return false;
            }else if(!left.toLowerCase(Locale.ROOT)
                .contains(expected.toLowerCase(Locale.ROOT)))return false;
        }
        return true;
    }
    void apply(List<Map<String,String>> rows){
        rows.removeIf(row->!matches(row));
        if(sortColumn.isEmpty())return;
        Comparator<Map<String,String>> byValue=(a,b)->compare(
            str(a.get(sortColumn)),str(b.get(sortColumn)));
        if(descending)byValue=byValue.reversed();
        rows.sort(byValue);
    }
    static String str(Object value){
        return value==null?"":String.valueOf(value).trim();
    }
    private static Double number(String text){
        if(text==null||text.isEmpty())return null;
        String raw=text.trim().replace(",","").replace("₩","")
            .replace("원","").replace("%p","").replace("%","").replace("점","");
        if(!raw.matches("[+-]?[0-9]+(\\.[0-9]+)?"))return null;
        try{return Double.valueOf(raw);}catch(NumberFormatException ignored){return null;}
    }
    static int compare(String one,String two){
        Double a=number(one),b=number(two);
        if(a!=null&&b!=null)return Double.compare(a,b);
        return str(one).compareToIgnoreCase(str(two));
    }
}
