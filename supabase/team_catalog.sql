-- TECS shared manufacturer catalogue. Run once in the Supabase SQL Editor.
-- Temporary open catalogue: anyone with the app's publishable key can read or update.

create table if not exists public.tecs_catalog_products (
  identity text primary key,
  brand text not null,
  product_name text not null,
  product_code text,
  product_url text not null,
  product_json jsonb not null,
  verified_at timestamptz not null,
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid() references auth.users(id)
);

create index if not exists tecs_catalog_products_brand_idx
  on public.tecs_catalog_products (brand);
create index if not exists tecs_catalog_products_verified_idx
  on public.tecs_catalog_products (verified_at desc);

alter table public.tecs_catalog_products enable row level security;
grant select, insert, update on table public.tecs_catalog_products to anon;
grant select, insert, update on table public.tecs_catalog_products to authenticated;

drop policy if exists "TECS team can read catalogue" on public.tecs_catalog_products;
create policy "TECS team can read catalogue"
  on public.tecs_catalog_products for select
  to anon, authenticated
  using (true);

drop policy if exists "TECS team can add catalogue" on public.tecs_catalog_products;
create policy "TECS team can add catalogue"
  on public.tecs_catalog_products for insert
  to anon, authenticated
  with check (true);

drop policy if exists "TECS team can update catalogue" on public.tecs_catalog_products;
create policy "TECS team can update catalogue"
  on public.tecs_catalog_products for update
  to anon, authenticated
  using (true)
  with check (true);
