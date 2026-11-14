package com.artitrade.mobile;
import java.util.*;

public final class ColumnQueryV036Test {
    static void eq(Object expected,Object actual,String msg){
        if(!Objects.equals(expected,actual))throw new AssertionError(msg+": "+actual);
    }
    public static void main(String[] args){
        ColumnQueryV036 model=new ColumnQueryV036("name","when","price","result");
        model.move("result",-1);
        eq(Arrays.asList("name","when","result","price"),model.columns(),"reorder column");
        model.hide("when",true);
        eq(Arrays.asList("name","result","price"),model.visibleColumns(),"toggle column");
        model.setFilter("name","삼성");
        model.setFilter("result","O");
        Map<String,String> one=new HashMap<>();
        one.put("name","삼성전자");one.put("result","O");one.put("price","120000");
        Map<String,String> two=new HashMap<>(one);two.put("result","X");
        Map<String,String> three=new HashMap<>(one);three.put("name","하이닉스");
        eq(true,model.matches(one),"pass filters");
        eq(false,model.matches(two),"remove mismatched goal");
        eq(false,model.matches(three),"remove mismatched name");
        model.setFilter("result","");
        model.setFilter("name","");
        model.sort("price"); 
        Map<String,String> low=new HashMap<>(one);low.put("price","5000");
        List<Map<String,String>> xs=new ArrayList<>(Arrays.asList(one,low));
        model.apply(xs);
        eq("5000",xs.get(0).get("price"),"numeric ascending");
        model.sort("price");
        model.apply(xs);
        eq("120000",xs.get(0).get("price"),"numeric descending");
        model.setFilter("name","=삼성전자");
        eq(true,model.matches(one),"exact column match");
        eq(false,model.matches(three),"exact mismatch");
        eq(true,model.columns().contains("when"),"hide is not delete");
        System.out.println("COLUMN_QUERY_V036_FILTER_SORT_MOVE_PASS");
    }
}
