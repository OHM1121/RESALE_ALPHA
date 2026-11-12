package com.artitrade.mobile;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.security.MessageDigest;
import java.security.SecureRandom;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** Local Android Keystore encryption. No broker credentials are bundled in the APK. */
final class SecureBox {
    private static final String ALIAS = "artitrade_hub_aes_v1";
    private final Context context;
    SecureBox(Context context){ this.context=context.getApplicationContext(); }

    private synchronized SecretKey key() throws Exception {
        KeyStore store=KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        if (!store.containsAlias(ALIAS)){
            KeyGenerator generator=KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,"AndroidKeyStore");
            generator.init(new KeyGenParameterSpec.Builder(ALIAS,
                    KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                    .setKeySize(256)
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setRandomizedEncryptionRequired(true).build());
            generator.generateKey();
            store.load(null);
        }
        return ((KeyStore.SecretKeyEntry)store.getEntry(ALIAS,null)).getSecretKey();
    }
    synchronized String seal(String source) {
        try {
            Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE,key());
            byte[] iv=cipher.getIV(), ct=cipher.doFinal(source.getBytes(StandardCharsets.UTF_8));
            byte[] output=new byte[iv.length+ct.length];
            System.arraycopy(iv,0,output,0,iv.length);
            System.arraycopy(ct,0,output,iv.length,ct.length);
            return Base64.encodeToString(output,Base64.NO_WRAP);
        }catch(Exception e){throw new IllegalStateException("Keystore encryption unavailable",e);}
    }
    synchronized String unseal(String cipherText){
        try {
            byte[] raw=Base64.decode(cipherText,Base64.NO_WRAP);
            if(raw.length<29) throw new IllegalArgumentException("Invalid encrypted data");
            byte[] iv=new byte[12], enc=new byte[raw.length-12];
            System.arraycopy(raw,0,iv,0,12); System.arraycopy(raw,12,enc,0,enc.length);
            Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE,key(),new GCMParameterSpec(128,iv));
            return new String(cipher.doFinal(enc),StandardCharsets.UTF_8);
        }catch(Exception e){throw new IllegalStateException("Keystore decryption unavailable",e);}
    }
    private SharedPreferences prefs(){return context.getSharedPreferences("hub_secret_box",Context.MODE_PRIVATE);}
    synchronized String token(){
        String encrypted=prefs().getString("access_token",null);
        if(encrypted!=null) return unseal(encrypted);
        return rotateToken();
    }
    synchronized String rotateToken(){
        byte[] bytes=new byte[32]; new SecureRandom().nextBytes(bytes);
        StringBuilder result=new StringBuilder(64);
        for(byte b:bytes) result.append(String.format(java.util.Locale.ROOT,"%02x",b&255));
        String value=result.toString();
        if(!prefs().edit().putString("access_token",seal(value)).commit())
            throw new IllegalStateException("Failed to persist pairing token");
        return value;
    }
    boolean compareToken(String provided){
        if(provided==null || provided.length()>256) return false;
        return MessageDigest.isEqual(token().getBytes(StandardCharsets.US_ASCII),
                provided.getBytes(StandardCharsets.US_ASCII));
    }
}
