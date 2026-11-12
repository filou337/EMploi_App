-- 010 : nom_compact retire correctement deux mots génériques consécutifs (« AXA en France » -> axa).
CREATE OR REPLACE FUNCTION nom_compact(nom TEXT) RETURNS TEXT LANGUAGE sql IMMUTABLE AS $$
    SELECT coalesce(nullif(replace(regexp_replace(
        regexp_replace(translate(lower(coalesce(nom, '')),
            'àâäáãéèêëíìîïóòôöõúùûüçñÿ', 'aaaaaeeeeiiiiooooouuuucny'), '[^a-z0-9]+', ' ', 'g'),
        '\m(sa|sas|sasu|sarl|eurl|se|inc|ltd|llc|gmbh|group|groupe|france|en)\M', '', 'g'),
        ' ', ''), ''),
        regexp_replace(lower(coalesce(nom, '')), '[^a-z0-9]', '', 'g'))
$$;
